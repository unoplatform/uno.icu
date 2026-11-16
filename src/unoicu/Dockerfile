# Build this Dockerfile with  "docker build --build-arg EMSCRIPTEN_VERSION=<your-version> --build-arg IS_MULTITHREADED=<true|false> --build-arg IS_SIMD_SUPPORTED=<true|false> --output type=local,dest=. ." to get the unoicu.a file in the current directory
ARG EMSCRIPTEN_VERSION
ARG BUILD_IMAGE=docker.io/emscripten/emsdk:${EMSCRIPTEN_VERSION}

FROM ${BUILD_IMAGE} AS build

RUN apt-get update && apt-get install -y make git python3 doxygen zip wget g++

WORKDIR /icu

COPY icu.zip .
RUN echo "4d776b33e5016b259b96f86135264f425e5703543d9cfb0cf23f0d923665a742  icu.zip" | sha256sum --check --strict
RUN unzip icu.zip
WORKDIR icu-457157a92aa053e632cc7fcfd0e12f8a943b2d11/icu4c/source

ARG IS_MULTITHREADED
ARG IS_SIMD_SUPPORTED


# Thanks to https://github.com/echogarden-project/icu-segmentation-wasm/blob/1f0c7381f76e0d38784dd6ad23fed5f6365da319/docs/Building.md
# for documenting the necessary configure options

RUN emconfigure ./configure \
    --enable-static \
    --disable-shared \
    --with-data-packaging=static \
    --disable-tests \
    --disable-samples \
    --disable-tools \
    --disable-extras \
    CFLAGS="-O3 -std=c11 $([ "$IS_MULTITHREADED" = "true" ] && echo "-pthread") $([ "$IS_SIMD_SUPPORTED" = "true" ] && echo "-msimd128")" \
    CXXFLAGS="-O3 -std=c++17 $([ "$IS_MULTITHREADED" = "true" ] && echo "-pthread") $([ "$IS_SIMD_SUPPORTED" = "true" ] && echo "-msimd128")"
RUN emmake make -j $(nproc)

COPY unoicu.c .
RUN emcc -I common -c unoicu.c
RUN emar x lib/libicuuc.a
RUN emar x stubdata/libicudata.a
RUN emar rc unoicu.a *.ao unoicu.o
RUN python3 -c "import pathlib,subprocess; p=pathlib.Path('/build-evidence'); p.mkdir(); [(p/name).write_bytes(subprocess.check_output(cmd)) for name,cmd in [('packages.txt',['dpkg-query','-W']),('compiler.txt',['emcc','--version']),('make.txt',['make','--version'])]]; (p/'config.log').write_bytes(pathlib.Path('config.log').read_bytes())"

FROM scratch
COPY --from=build /icu/icu-457157a92aa053e632cc7fcfd0e12f8a943b2d11/icu4c/source/unoicu.a /
COPY --from=build /build-evidence /build-evidence
