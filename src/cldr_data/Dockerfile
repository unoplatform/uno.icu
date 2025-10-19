# Build this Dockerfile with  "docker build --build-arg ICU_SOURCE_ZIP_URL=<icu-zip-url> --output type=local,dest=. ." to get the .dat file in the currect directory
ARG ICU_SOURCE_ZIP_URL

FROM docker.io/ubuntu:24.04 AS build
ARG ICU_SOURCE_ZIP_URL

RUN apt-get update && apt-get install -y make git python3 doxygen zip wget g++

WORKDIR /icu

ENV ICU_DATA_FILTER_FILE=/icu/filters_wasm.json
COPY filters_wasm.json .

RUN wget ${ICU_SOURCE_ZIP_URL}
RUN unzip release-77-1.zip
WORKDIR icu-release-77-1/icu4c/source

RUN ./runConfigureICU Linux
RUN make -j $(nproc)


FROM scratch
COPY --from=build /icu/icu-release-77-1/icu4c/source/data/out/tmp/icudt77l.dat /icudt.dat
