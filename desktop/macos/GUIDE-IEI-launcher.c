/* Universal macOS launcher shim for unsigned GUIDE-IEI releases.
 *
 * The release build compiles this for arm64 and x86_64. Keeping the real
 * launcher logic in Contents/Resources/launcher.sh makes it testable without
 * Xcode while giving LaunchServices an actual native executable, so a clean
 * Apple-silicon Mac never asks for Rosetta merely to run a Bash script.
 */
#include <mach-o/dyld.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char *argv[]) {
    uint32_t size = 0;
    char *executable = NULL;
    char *slash = NULL;
    char script[PATH_MAX];
    char **shell_argv = NULL;
    int index = 0;

    (void)_NSGetExecutablePath(NULL, &size);
    executable = malloc((size_t)size);
    if (executable == NULL || _NSGetExecutablePath(executable, &size) != 0) {
        fputs("GUIDE-IEI: cannot resolve the application path\n", stderr);
        free(executable);
        return 1;
    }
    slash = strrchr(executable, '/');
    if (slash == NULL) {
        fputs("GUIDE-IEI: invalid application path\n", stderr);
        free(executable);
        return 1;
    }
    *slash = '\0';
    if (snprintf(script, sizeof(script), "%s/../Resources/launcher.sh", executable)
            >= (int)sizeof(script)) {
        fputs("GUIDE-IEI: application path is too long\n", stderr);
        free(executable);
        return 1;
    }
    free(executable);

    shell_argv = calloc((size_t)argc + 2, sizeof(char *));
    if (shell_argv == NULL) {
        fputs("GUIDE-IEI: cannot allocate launcher arguments\n", stderr);
        return 1;
    }
    shell_argv[0] = "/bin/bash";
    shell_argv[1] = script;
    for (index = 1; index < argc; index++) {
        shell_argv[index + 1] = argv[index];
    }
    shell_argv[argc + 1] = NULL;
    execv("/bin/bash", shell_argv);
    perror("GUIDE-IEI: cannot start /bin/bash");
    free(shell_argv);
    return 1;
}
