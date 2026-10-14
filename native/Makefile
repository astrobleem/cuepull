CC ?= cc
CFLAGS ?= -O2 -std=c99 -Wall -Wextra -Werror -pedantic
CPPFLAGS ?= -D_FILE_OFFSET_BITS=64
all: cuepull32-cli
cuepull32-cli: core.c platform.c cli.c cuepull.h platform.h
	$(CC) $(CPPFLAGS) $(CFLAGS) core.c platform.c cli.c -o $@
test: cuepull32-cli
	python3 tests/test_native.py --exe ./cuepull32-cli
clean:
	rm -f cuepull32-cli
.PHONY: all test clean
