/* Classic ANSI Win32 frontend. No Python, COM, themes or newer shell APIs. */
#include "cuepull.h"
#include <windows.h>
#include <commdlg.h>
#include <shellapi.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define ID_IMAGE 101
#define ID_OUTPUT 102
#define ID_BROWSE 103
#define ID_EXTRACT 104
#define ID_CANCEL 105
#define ID_ABOUT 106
#define WM_PROGRESS (WM_APP + 1)
#define WM_FINISHED (WM_APP + 2)
#define WM_AUTO (WM_APP + 3)
#define WM_TEST_DROP (WM_APP + 4)

typedef struct gui_job {
    cp_disc disc;
    char input[CP_PATH];
    char output[CP_PATH];
    char status[CP_ERROR];
    cp_u64 done, total;
    int success;
    volatile LONG cancel;
    DWORD reported_tick;
} gui_job;

static HINSTANCE instance;
static HWND window, image_edit, output_edit, status_text;
static HWND browse_button, extract_button, cancel_button;
static HANDLE worker;
static CRITICAL_SECTION lock;
static gui_job *job;
static char startup[CP_PATH];
static unsigned percentage;
static HFONT font;

static int progress(void *user, cp_u64 done, cp_u64 total, const char *path)
{
    gui_job *current;
    current = (gui_job *)user;
    EnterCriticalSection(&lock);
    current->done = done; current->total = total;
    strncpy(current->status, path, CP_ERROR - 1);
    current->status[CP_ERROR - 1] = 0;
    LeaveCriticalSection(&lock);
    if (GetTickCount() - current->reported_tick >= 100 || (total && done == total)) {
        current->reported_tick = GetTickCount();
        PostMessageA(window, WM_PROGRESS, 0, 0);
    }
    return InterlockedExchangeAdd(&current->cancel, 0) == 0;
}

static DWORD WINAPI run_job(LPVOID user)
{
    gui_job *current;
    int ok;
    char count[32], bytes[32];
    current = (gui_job *)user;
    ok = cp_open(&current->disc, current->input, NULL, 0, progress, current);
    if (ok && !current->output[0]) ok = cp_default_destination(&current->disc, current->output);
    if (ok) ok = cp_extract(&current->disc, current->output);
    if (ok) {
        progress(current, current->disc.data_bytes, current->disc.data_bytes,
                 "Verifying file lengths and source extent samples...");
        ok = cp_verify(&current->disc, current->output);
    }
    EnterCriticalSection(&lock);
    current->success = ok;
    if (ok) {
        cp_decimal(current->disc.files, count); cp_decimal(current->disc.data_bytes, bytes);
        sprintf(current->status, "Complete: %s files, %s bytes. Source extent samples match.\r\n%s",
                count, bytes, current->output);
    } else {
        strncpy(current->status, current->disc.error, CP_ERROR - 1);
        current->status[CP_ERROR - 1] = 0;
    }
    LeaveCriticalSection(&lock);
    cp_close(&current->disc);
    PostMessageA(window, WM_FINISHED, 0, 0);
    return 0;
}

static void set_busy(int busy)
{
    EnableWindow(image_edit, !busy); EnableWindow(output_edit, !busy);
    EnableWindow(browse_button, !busy); EnableWindow(extract_button, !busy);
    EnableWindow(cancel_button, busy);
}

static void begin_extract(void)
{
    DWORD thread_id;
    if (worker) return;
    if (job) { free(job); job = NULL; }
    job = (gui_job *)calloc(1, sizeof(*job));
    if (!job) { MessageBoxA(window, "Out of memory.", "CuePull32", MB_ICONERROR); return; }
    GetWindowTextA(image_edit, job->input, CP_PATH);
    GetWindowTextA(output_edit, job->output, CP_PATH);
    if (!job->input[0]) {
        SetWindowTextA(status_text, "Choose or drop a BIN/CUE image first."); return;
    }
    percentage = 0; set_busy(1);
    SetWindowTextA(status_text, "Inspecting the disc before creating output...");
    worker = CreateThread(NULL, 0, run_job, job, 0, &thread_id);
    if (!worker) { set_busy(0); SetWindowTextA(status_text, "Cannot start extraction worker."); }
}

static void choose_image(void)
{
    OPENFILENAMEA dialog;
    char path[CP_PATH];
    memset(&dialog, 0, sizeof(dialog)); path[0] = 0;
    /* Original 32-bit OPENFILENAME layout accepted by Windows 95/98/NT. */
    dialog.lStructSize = 76; dialog.hwndOwner = window;
    dialog.lpstrFilter = "CD images (*.bin;*.cue;*.iso;*.img)\0*.bin;*.cue;*.iso;*.img\0All files\0*.*\0\0";
    dialog.lpstrFile = path; dialog.nMaxFile = CP_PATH;
    dialog.Flags = OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_HIDEREADONLY | OFN_NOCHANGEDIR;
    if (GetOpenFileNameA(&dialog)) {
        SetWindowTextA(image_edit, path); SetWindowTextA(output_edit, "");
        SetWindowTextA(status_text, "Ready. Extract creates a new sibling folder using the image name.");
    }
}

static HWND control(const char *type, const char *text, DWORD style,
                    int x, int y, int w, int h, int id)
{
    HWND result;
    result = CreateWindowA(type, text, WS_CHILD | WS_VISIBLE | style,
                          x, y, w, h, window, (HMENU)id, instance, NULL);
    SendMessageA(result, WM_SETFONT, (WPARAM)font, TRUE);
    return result;
}

static LRESULT CALLBACK window_proc(HWND hwnd, UINT message,
                                    WPARAM wparam, LPARAM lparam)
{
    char status[CP_ERROR], path[CP_PATH];
    switch (message) {
    case WM_CREATE:
        window = hwnd;
        font = (HFONT)GetStockObject(DEFAULT_GUI_FONT);
        control("STATIC", "CuePull32 - native BIN/CUE extraction", 0, 18, 16, 590, 22, 0);
        control("STATIC", "Drop an image here, then click Extract. No CUE is needed for supported ISO9660 images.",
                0, 18, 45, 590, 34, 0);
        control("STATIC", "Image:", 0, 18, 90, 70, 20, 0);
        image_edit = control("EDIT", "", WS_BORDER | ES_AUTOHSCROLL | WS_TABSTOP,
                             88, 86, 430, 24, ID_IMAGE);
        SendMessageA(image_edit, EM_LIMITTEXT, CP_PATH - 1, 0);
        browse_button = control("BUTTON", "Browse...", WS_TABSTOP, 526, 86, 83, 24, ID_BROWSE);
        control("STATIC", "Output:", 0, 18, 129, 70, 20, 0);
        output_edit = control("EDIT", "", WS_BORDER | ES_AUTOHSCROLL | WS_TABSTOP,
                              88, 125, 521, 24, ID_OUTPUT);
        SendMessageA(output_edit, EM_LIMITTEXT, CP_PATH - 1, 0);
        control("STATIC", "Leave Output blank for image-name folder; existing names get (2), (3), etc.",
                0, 88, 156, 520, 30, 0);
        extract_button = control("BUTTON", "Extract", WS_TABSTOP | BS_DEFPUSHBUTTON,
                                 18, 194, 105, 29, ID_EXTRACT);
        cancel_button = control("BUTTON", "Cancel", WS_TABSTOP, 134, 194, 90, 29, ID_CANCEL);
        control("BUTTON", "About", WS_TABSTOP, 519, 194, 90, 29, ID_ABOUT);
        EnableWindow(cancel_button, FALSE);
        status_text = control("STATIC", "Choose an image to begin. Candidate build: XP/98 guest qualification is pending.",
                              SS_LEFT, 18, 268, 590, 76, 0);
        DragAcceptFiles(hwnd, TRUE);
        if (startup[0]) { SetWindowTextA(image_edit, startup); PostMessageA(hwnd, WM_AUTO, 0, 0); }
        return 0;
    case WM_COMMAND:
        if (LOWORD(wparam) == ID_BROWSE && !worker) choose_image();
        if (LOWORD(wparam) == ID_EXTRACT) begin_extract();
        if (LOWORD(wparam) == ID_CANCEL && job) InterlockedExchange(&job->cancel, 1);
        if (LOWORD(wparam) == ID_ABOUT) {
            char about[1024];
            strcpy(about, "CuePull32 " CP_VERSION "\r\nXP/98 guest qualification pending.\r\n\r\n");
            strcat(about, cp_build_notice());
            MessageBoxA(hwnd, about, "About CuePull32", MB_OK | MB_ICONINFORMATION);
        }
        return 0;
    case WM_AUTO:
        begin_extract(); return 0;
    case WM_TEST_DROP:
        /* Test harness: create the HDROP inside its owning process, then
         * exercise the real Shell32 WM_DROPFILES handler below. Windows does
         * not marshal an arbitrary Python process's HGLOBAL across processes. */
        if (!worker) {
            HGLOBAL memory;
            unsigned char *bytes;
            unsigned length;
            GetWindowTextA(image_edit, path, CP_PATH); length = (unsigned)strlen(path);
            memory = GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, 20 + length + 2);
            if (memory) {
                bytes = (unsigned char *)GlobalLock(memory);
                if (bytes) {
                    *(DWORD *)bytes = 20; memcpy(bytes + 20, path, length);
                    GlobalUnlock(memory); SendMessageA(hwnd, WM_DROPFILES, (WPARAM)memory, 0);
                } else GlobalFree(memory);
            }
        }
        return 0;
    case WM_DROPFILES:
        if (!worker) {
            HDROP drop;
            drop = (HDROP)wparam;
            if (DragQueryFileA(drop, 0xffffffffUL, NULL, 0) != 1 ||
                DragQueryFileA(drop, 0, NULL, 0) >= CP_PATH) {
                SetWindowTextA(status_text, "Drop one local BIN or CUE shorter than MAX_PATH.");
            } else {
                DragQueryFileA(drop, 0, path, CP_PATH);
                SetWindowTextA(image_edit, path); SetWindowTextA(output_edit, "");
                SetWindowTextA(status_text, "Image selected. Click Extract to begin.");
            }
        }
        DragFinish((HDROP)wparam); return 0;
    case WM_PROGRESS:
        if (job) {
            EnterCriticalSection(&lock);
            strcpy(status, job->status);
            percentage = job->total ? (unsigned)(job->done * 100 / job->total) : 0;
            LeaveCriticalSection(&lock);
            SetWindowTextA(status_text, status); InvalidateRect(hwnd, NULL, FALSE);
        }
        return 0;
    case WM_FINISHED:
        if (worker) { CloseHandle(worker); worker = NULL; }
        set_busy(0);
        EnterCriticalSection(&lock); strcpy(status, job->status);
        LeaveCriticalSection(&lock);
        SetWindowTextA(status_text, status); InvalidateRect(hwnd, NULL, FALSE);
        return 0;
    case WM_PAINT:
        {
            PAINTSTRUCT paint;
            RECT outer, filled;
            HDC dc;
            HBRUSH brush;
            dc = BeginPaint(hwnd, &paint);
            SetRect(&outer, 18, 239, 609, 258); FrameRect(dc, &outer, (HBRUSH)GetStockObject(GRAY_BRUSH));
            SetRect(&filled, 20, 241, 607, 256);
            FillRect(dc, &filled, (HBRUSH)(COLOR_BTNFACE + 1));
            SetRect(&filled, 20, 241, 20 + (int)(587 * percentage / 100), 256);
            brush = CreateSolidBrush(RGB(32, 96, 160)); FillRect(dc, &filled, brush); DeleteObject(brush);
            EndPaint(hwnd, &paint);
        }
        return 0;
    case WM_CLOSE:
        if (worker) {
            InterlockedExchange(&job->cancel, 1);
            SetWindowTextA(status_text, "Cancelling... close again after the worker stops."); return 0;
        }
        DestroyWindow(hwnd); return 0;
    case WM_DESTROY:
        DragAcceptFiles(hwnd, FALSE); PostQuitMessage(0); return 0;
    }
    return DefWindowProcA(hwnd, message, wparam, lparam);
}

int WINAPI WinMain(HINSTANCE app, HINSTANCE previous, LPSTR command, int show)
{
    WNDCLASSA klass;
    MSG message;
    char *p;
    unsigned n;
    (void)previous; instance = app;
    /* A single Explorer argument starts automatic sibling-folder extraction. */
    p = command; while (*p == ' ') ++p; n = 0;
    if (*p == '"') {
        ++p; while (*p && *p != '"' && n < CP_PATH - 1) startup[n++] = *p++;
        if (*p == '"') ++p;
    } else while (*p && *p != ' ' && n < CP_PATH - 1) startup[n++] = *p++;
    startup[n] = 0; while (*p == ' ') ++p;
    if (*p) { MessageBoxA(NULL, "Provide one image path. Drop one BIN or CUE onto the EXE.", "CuePull32", MB_ICONERROR); return 2; }
    InitializeCriticalSection(&lock);
    memset(&klass, 0, sizeof(klass)); klass.lpfnWndProc = window_proc;
    klass.hInstance = app; klass.hCursor = LoadCursor(NULL, IDC_ARROW);
    klass.hbrBackground = (HBRUSH)(COLOR_BTNFACE + 1);
    klass.lpszClassName = "CuePull32Window";
    if (!RegisterClassA(&klass)) return 1;
    window = CreateWindowA(klass.lpszClassName, "CuePull32 - candidate", WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX,
                          CW_USEDEFAULT, CW_USEDEFAULT, 645, 390, NULL, NULL, app, NULL);
    if (!window) return 1;
    ShowWindow(window, show); UpdateWindow(window);
    while (GetMessageA(&message, NULL, 0, 0) > 0) {
        if (!IsDialogMessageA(window, &message)) {
            TranslateMessage(&message); DispatchMessageA(&message);
        }
    }
    if (job) free(job); DeleteCriticalSection(&lock);
    return (int)message.wParam;
}
