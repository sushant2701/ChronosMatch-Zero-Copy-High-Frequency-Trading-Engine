"""
Setup configuration for ChronosMatch with optional Cython extension build.

Zero-Copy High-Frequency Trading Engine.

Portability Rationale:
- If Cython and a C compiler (MSVC on Windows, GCC/Clang on POSIX) are installed,
  this script compiles `chronosmatch/engine_cy.pyx` into an optimized native C extension.
- If either Cython or a C compiler is unavailable, compilation is safely skipped and
  the package installs as pure-Python, falling back transparently to LimitOrderBook.
"""

import os
import sys
from setuptools import Extension, find_packages, setup
from setuptools.command.build_ext import build_ext

# Error types indicating compiler or environment absence
try:
    from setuptools.errors import (
        CCompilerError,
        DistutilsExecError,
        DistutilsPlatformError,
    )
except ImportError:
    from distutils.errors import (  # type: ignore
        CCompilerError,
        DistutilsExecError,
        DistutilsPlatformError,
    )


class OptionalBuildExt(build_ext):
    """
    Custom build_ext command that allows C extension compilation to fail gracefully.

    If building the native C extension fails due to missing C compilers (e.g. MSVC Build Tools
    on Windows) or environment restrictions, this handler catches the failure, logs a clear warning,
    and allows the installation to proceed as pure-Python without failing the build.
    """

    def run(self) -> None:
        try:
            super().run()
        except (DistutilsPlatformError, FileNotFoundError) as exc:
            print(f"\n[ChronosMatch Build Notice] Native extension build skipped: {exc}")
            print("[ChronosMatch Build Notice] Engine will run using the pure-Python fallback backend.\n")

    def build_extension(self, ext: Extension) -> None:
        try:
            super().build_extension(ext)
        except (
            CCompilerError,
            DistutilsExecError,
            DistutilsPlatformError,
            ValueError,
            Exception,
        ) as exc:
            print(f"\n[ChronosMatch Build Notice] Failed to compile native extension '{ext.name}': {exc}")
            print("[ChronosMatch Build Notice] Falling back to pure-Python engine backend.\n")


# Detect Cython availability
ext_modules = []
HAVE_CYTHON = False

try:
    from Cython.Build import cythonize

    HAVE_CYTHON = True
except ImportError:
    pass

pyx_path = os.path.join("chronosmatch", "engine_cy.pyx")
c_path = os.path.join("chronosmatch", "engine_cy.c")

if HAVE_CYTHON and os.path.exists(pyx_path):
    ext_modules = cythonize(
        [
            Extension(
                "chronosmatch.engine_cy",
                sources=[pyx_path],
                extra_compile_args=["/O2"] if sys.platform == "win32" else ["-O3", "-march=native"],
            )
        ],
        compiler_directives={
            "language_level": "3",
            "boundscheck": False,
            "wraparound": False,
            "cdivision": True,
        },
    )
elif os.path.exists(c_path):
    # Pre-cythonized C source distribution fallback
    ext_modules = [
        Extension(
            "chronosmatch.engine_cy",
            sources=[c_path],
            extra_compile_args=["/O2"] if sys.platform == "win32" else ["-O3", "-march=native"],
        )
    ]
else:
    # No Cython installed and no generated C file: install pure Python
    ext_modules = []

setup(
    ext_modules=ext_modules,
    cmdclass={"build_ext": OptionalBuildExt},
)
