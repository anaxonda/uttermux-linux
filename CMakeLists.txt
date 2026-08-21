cmake_minimum_required(VERSION 3.20)
project(uttermux VERSION 0.4.0 LANGUAGES C CXX)

include(GNUInstallDirs)
include(CTest)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(Threads REQUIRED)
find_package(PkgConfig REQUIRED)
pkg_check_modules(RUBBERBAND REQUIRED IMPORTED_TARGET rubberband)

find_path(SPEECHD_INCLUDE_DIR speech-dispatcher/spd_module_main.h REQUIRED)
find_library(SPEECHD_MODULE_LIBRARY speechd_module REQUIRED)

add_library(sdsherpa_core
  src/config.cpp
  src/engine.cpp
  src/audio.cpp
)
target_include_directories(sdsherpa_core PUBLIC include)
target_link_libraries(sdsherpa_core PUBLIC Threads::Threads PkgConfig::RUBBERBAND ${CMAKE_DL_LIBS})
target_compile_options(sdsherpa_core PRIVATE -Wall -Wextra -Wpedantic)

add_library(uttermux_protocol src/protocol.cpp src/text.cpp)
target_include_directories(uttermux_protocol PUBLIC include)
target_compile_options(uttermux_protocol PRIVATE -Wall -Wextra -Wpedantic)

add_executable(sd_uttermux src/uttermux_module.cpp)
target_include_directories(sd_uttermux PRIVATE include ${SPEECHD_INCLUDE_DIR} ${SPEECHD_INCLUDE_DIR}/speech-dispatcher)
target_link_libraries(sd_uttermux PRIVATE sdsherpa_core uttermux_protocol ${SPEECHD_MODULE_LIBRARY})
target_compile_options(sd_uttermux PRIVATE -Wall -Wextra -Wpedantic)

set(SPEECHD_MODULE_DIR "${CMAKE_INSTALL_LIBDIR}/speech-dispatcher/speech-dispatcher-modules"
    CACHE PATH "Speech Dispatcher output module directory")
install(TARGETS sd_uttermux RUNTIME DESTINATION ${SPEECHD_MODULE_DIR})
install(PROGRAMS daemon/uttermuxd.py DESTINATION ${CMAKE_INSTALL_LIBDIR}/uttermux RENAME uttermuxd)
install(PROGRAMS bridge/koreader_bridge.py DESTINATION ${CMAKE_INSTALL_LIBDIR}/uttermux RENAME koreader-bridge)
foreach(unit uttermux.service uttermux-koreader.service uttermux-tray.service)
  configure_file(systemd/${unit} ${CMAKE_CURRENT_BINARY_DIR}/systemd/${unit} @ONLY)
endforeach()
set(SYSTEMD_USER_UNIT_DIR "${CMAKE_INSTALL_PREFIX}/lib/systemd/user"
    CACHE PATH "systemd user unit directory")
install(FILES systemd/uttermux.socket
        ${CMAKE_CURRENT_BINARY_DIR}/systemd/uttermux.service
        ${CMAKE_CURRENT_BINARY_DIR}/systemd/uttermux-koreader.service
        ${CMAKE_CURRENT_BINARY_DIR}/systemd/uttermux-tray.service
        DESTINATION ${SYSTEMD_USER_UNIT_DIR})
install(PROGRAMS cli/uttermux DESTINATION ${CMAKE_INSTALL_BINDIR})
install(PROGRAMS ui/uttermux-panel.py DESTINATION ${CMAKE_INSTALL_BINDIR} RENAME uttermux-panel)
install(PROGRAMS ui/uttermux-app.py DESTINATION ${CMAKE_INSTALL_BINDIR} RENAME uttermux-app)
install(PROGRAMS ui/uttermux-tray.py DESTINATION ${CMAKE_INSTALL_BINDIR} RENAME uttermux-tray)
install(PROGRAMS cli/sherpa-voice DESTINATION ${CMAKE_INSTALL_LIBDIR}/uttermux RENAME model-manager)
install(PROGRAMS scripts/install-qwen DESTINATION ${CMAKE_INSTALL_LIBDIR}/uttermux)
install(PROGRAMS scripts/install-moss DESTINATION ${CMAKE_INSTALL_LIBDIR}/uttermux)
install(PROGRAMS providers/moss_server.py DESTINATION ${CMAKE_INSTALL_LIBDIR}/uttermux RENAME moss-server)
install(FILES python/uttermux_profiles.py DESTINATION ${CMAKE_INSTALL_LIBDIR}/uttermux)
install(FILES catalog/catalog.toml catalog/v2/catalog.json
        DESTINATION ${CMAKE_INSTALL_DATADIR}/uttermux)
install(FILES config/uttermux.conf DESTINATION ${CMAKE_INSTALL_DATADIR}/speech-dispatcher/conf/modules)
install(FILES data/io.uttermux.App.desktop DESTINATION ${CMAKE_INSTALL_DATADIR}/applications)

if(BUILD_TESTING)
  add_executable(core_tests tests/core_tests.cpp)
  target_link_libraries(core_tests PRIVATE sdsherpa_core uttermux_protocol)
  add_test(NAME core_tests COMMAND core_tests)
  add_executable(engine_smoke tests/engine_smoke.cpp)
  target_link_libraries(engine_smoke PRIVATE sdsherpa_core)
  find_package(Python3 COMPONENTS Interpreter REQUIRED)
  add_test(NAME cli_tests COMMAND ${Python3_EXECUTABLE} -m unittest discover -s ${CMAKE_SOURCE_DIR}/tests -p test_*.py)
  add_test(NAME generated_catalog
           COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/scripts/catalog/build_catalog.py
                   --piper-source ${CMAKE_SOURCE_DIR}/catalog/sources/piper.json --check)
endif()
