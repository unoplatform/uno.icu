# First use eng/provenance.py fetch-source, then copy icu.zip into this context.

ARG BUILD_IMAGE=docker.io/ubuntu:24.04
FROM ${BUILD_IMAGE} AS build

RUN apt-get update && apt-get install -y make git python3 doxygen zip wget g++

WORKDIR /icu

ENV ICU_DATA_FILTER_FILE=/icu/filters.json
COPY filters.json .

COPY icu.zip .
RUN echo "4d776b33e5016b259b96f86135264f425e5703543d9cfb0cf23f0d923665a742  icu.zip" | sha256sum --check --strict
RUN unzip icu.zip
WORKDIR icu-457157a92aa053e632cc7fcfd0e12f8a943b2d11/icu4c/source

RUN ./runConfigureICU Linux
RUN make -j $(nproc)
RUN python3 -c "import pathlib,subprocess; p=pathlib.Path('/build-evidence'); p.mkdir(); [(p/name).write_bytes(subprocess.check_output(cmd)) for name,cmd in [('packages.txt',['dpkg-query','-W']),('compiler.txt',['g++','--version']),('make.txt',['make','--version'])]]; (p/'config.log').write_bytes(pathlib.Path('config.log').read_bytes())"


FROM scratch
COPY --from=build /icu/icu-457157a92aa053e632cc7fcfd0e12f8a943b2d11/icu4c/source/data/out/tmp/icudt77l.dat /icudt.dat
COPY --from=build /build-evidence /build-evidence
