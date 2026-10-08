# squatmastr78 Mac SBPE

A community macOS port/customization of the StarBreak Plugin Engine (SBPE),
based on atomizer/sbpe.

## Target setup

- macOS on Apple Silicon, running the Intel/x86_64 StarBreak client through Rosetta
- Steam StarBreak installed in the normal Steam location
- Apple Command Line Tools installed
- Internet connection for first setup so Python can install `cffi`

## Install

1. Extract this ZIP somewhere in your home folder.
2. Quit StarBreak.
3. Double-click `BUILD.command`.
4. After a successful build, double-click `RUN.command`.

If macOS blocks a `.command` file, right-click it -> Open.

If Command Line Tools are missing, run:

    xcode-select --install

## Important

Do not run this at the same time as another SBPE/SBZoom-style injector.

This is an unofficial community modification.

## Credits / license

Based on atomizer/sbpe. The upstream SBPE README states that atomizer's code is
licensed under ISC, with third-party components retaining their own licenses.
The original README and third-party files are kept in this package; review
those files before redistributing modified copies.

Mac port/customization: squatmastr78.
