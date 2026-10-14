@echo off
setlocal
if not defined WATCOM set WATCOM=C:\WATCOM
set PATH=%WATCOM%\BINNT;%PATH%
set INCLUDE=%WATCOM%\H;%WATCOM%\H\NT
set LIB=%WATCOM%\LIB386;%WATCOM%\LIB386\NT
if not exist build mkdir build
cd build
wcl386 -q -bt=nt -l=nt -w4 -wx -ox -fe=cuepull32-cli.exe ..\core.c ..\platform.c ..\cli.c
if errorlevel 1 exit /b 1
rem GUI is a separate windowed executable built by the same static CRT.
if exist ..\win32.c (
  wcl386 -q -bt=nt -l=nt_win -w4 -wx -ox -fe=CuePull32.exe ..\core.c ..\platform.c ..\win32.c
  if errorlevel 1 exit /b 1
)
endlocal
