// AIDE native launcher
// Purpose: act as a proper Mach-O (universal) executable so macOS stops
// warning that this app will not run on Apple Silicon. All it does is
// locate the accompanying launcher.sh in Contents/Resources/ and exec
// /bin/bash on it, forwarding argv.
//
// Build (universal binary):
//   clang -arch arm64 -arch x86_64 -O2 \
//     -o AIDE.app/Contents/MacOS/AIDE \
//     AIDE.app/Contents/Resources/launcher.c

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>

int main(int argc, char *argv[]) {
    char exe_path[PATH_MAX];
    uint32_t size = sizeof(exe_path);
    if (_NSGetExecutablePath(exe_path, &size) != 0) {
        fprintf(stderr, "AIDE: executable path too long\n");
        return 1;
    }

    // Resolve any symlinks so relative paths work when launched from Finder.
    char real_exe[PATH_MAX];
    if (realpath(exe_path, real_exe) == NULL) {
        strncpy(real_exe, exe_path, sizeof(real_exe));
        real_exe[sizeof(real_exe) - 1] = '\0';
    }

    // real_exe => .../AIDE.app/Contents/MacOS/AIDE
    // We want => .../AIDE.app/Contents/Resources/launcher.sh
    char macos_dir[PATH_MAX];
    strncpy(macos_dir, real_exe, sizeof(macos_dir));
    macos_dir[sizeof(macos_dir) - 1] = '\0';
    char *macos = dirname(macos_dir);           // .../Contents/MacOS
    char contents_copy[PATH_MAX];
    strncpy(contents_copy, macos, sizeof(contents_copy));
    contents_copy[sizeof(contents_copy) - 1] = '\0';
    char *contents = dirname(contents_copy);    // .../Contents

    char script_path[PATH_MAX];
    int n = snprintf(script_path, sizeof(script_path),
                     "%s/Resources/launcher.sh", contents);
    if (n < 0 || n >= (int)sizeof(script_path)) {
        fprintf(stderr, "AIDE: script path too long\n");
        return 1;
    }

    if (access(script_path, R_OK) != 0) {
        fprintf(stderr, "AIDE: launcher.sh not found at %s\n", script_path);
        return 1;
    }

    // Build new argv: /bin/bash <script_path> <forwarded args...>
    char **new_argv = (char **)calloc((size_t)argc + 2, sizeof(char *));
    if (!new_argv) {
        fprintf(stderr, "AIDE: out of memory\n");
        return 1;
    }
    new_argv[0] = (char *)"/bin/bash";
    new_argv[1] = script_path;
    for (int i = 1; i < argc; i++) {
        new_argv[i + 1] = argv[i];
    }
    new_argv[argc + 1] = NULL;

    execv("/bin/bash", new_argv);
    perror("AIDE: execv /bin/bash failed");
    free(new_argv);
    return 1;
}
