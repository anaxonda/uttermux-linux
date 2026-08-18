#pragma once
/* Arch's libspeechd 0.12 package installs spd_module_main.h but omits the
 * compatibility header it includes.  Do not duplicate the ABI; use the
 * installed public audio-plugin definitions. */
#include <speech-dispatcher/spd_audio_plugin.h>
