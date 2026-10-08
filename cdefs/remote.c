// paths are relative to the build dir

#include "../cdefs/internals.h"
#include "../cdefs/generated.h" // needed to define enum values
#include "../cdefs/XDL.h"

#include "../libs/subhook/subhook.h"
#include "../libs/plthook/plthook.h"

#ifdef MS_WIN32
	#include "../libs/SDL/include/SDL.h"
#elif defined(__APPLE__)
	#include <stdint.h>
	#include "../cdefs/SDL.h"
#else
	#include <SDL2/SDL.h>
#endif

#ifndef MS_WIN32

#include <dlfcn.h>

#ifdef __APPLE__
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#endif

uint32_t kickstart(void);


#ifdef __APPLE__

static int sbpe_started = 0;

static void sbpe_start_once(void)
{
	if (sbpe_started)
		return;

	sbpe_started = 1;

	FILE *probe = fopen("/tmp/sbpe_constructor_hit.txt", "a");
	if (probe) {
		fputs("constructor/start entered\n", probe);
		fclose(probe);
	}

	char sbpe_slide[32];
	snprintf(
		sbpe_slide,
		sizeof(sbpe_slide),
		"0x%llx",
		(unsigned long long)_dyld_get_image_vmaddr_slide(0)
	);

	setenv("SBPE_SLIDE", sbpe_slide, 1);

	int result = kickstart();

	probe = fopen("/tmp/sbpe_constructor_hit.txt", "a");
	if (probe) {
		fprintf(probe, "kickstart returned %d\n", result);
		fclose(probe);
	}
}

__attribute__((constructor))
static void sbpe_constructor(void)
{
	sbpe_start_once();
}

#else

static void sbpe_start_once(void)
{
	kickstart();
}

#endif

CFFI_DLLEXPORT int SDL_Init(uint32_t flags) {
#ifdef __APPLE__
	FILE *probe = fopen("/tmp/sbpe_sdl_init_hit.txt", "w");
	if (probe) {
		fputs("remote.bin SDL_Init hook ran\n", probe);
		fclose(probe);
	}

	char sbpe_slide[32];
	snprintf(
		sbpe_slide,
		sizeof(sbpe_slide),
		"0x%llx",
		(unsigned long long)_dyld_get_image_vmaddr_slide(0)
	);
	setenv("SBPE_SLIDE", sbpe_slide, 1);
#endif
	sbpe_start_once();
	return ((int(*)(uint32_t))dlsym(RTLD_NEXT, "SDL_Init"))(flags);
}

#endif
