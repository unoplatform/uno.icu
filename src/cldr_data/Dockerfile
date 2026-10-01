# Build this Dockerfile with  "docker build --build-arg ICU_SOURCE_ZIP_URL=<icu-zip-url> --output type=local,dest=. ." to get the .dat files in the current directory
ARG ICU_SOURCE_ZIP_URL

FROM docker.io/ubuntu:24.04 AS build
ARG ICU_SOURCE_ZIP_URL

RUN apt-get update && apt-get install -y make git python3 doxygen zip wget g++

WORKDIR /icu

ENV ICU_DATA_FILTER_FILE=/icu/filters.json
COPY filters.json .

RUN wget ${ICU_SOURCE_ZIP_URL}
RUN unzip release-77-1.zip
WORKDIR icu-release-77-1/icu4c/source

RUN ./runConfigureICU Linux
RUN make -j $(nproc)

WORKDIR data/out/tmp
ENV LD_LIBRARY_PATH=/icu/icu-release-77-1/icu4c/source/lib

# Data the filter keeps for the build tools (genbrk can't initialize ICU without the converter aliases) but
# that text layout never reads: the main locale tree (break iterators use the brkitr tree) and the converter aliases.
RUN ../../../bin/icupkg --ignore-deps -l icudt77l.dat > items.txt \
    && grep -E '^[^/]+\.res$|^cnvalias\.icu$' items.txt > unused.txt \
    && test -s unused.txt \
    && ../../../bin/icupkg --ignore-deps -r unused.txt icudt77l.dat icudt77l-full.dat

# Split the break iterator dictionaries (Chinese/Japanese, Thai, Lao, Khmer, Burmese words: most of the data)
# from the rest, so they can be left out or loaded only when text in those scripts shows up.
RUN grep '\.dict$' items.txt > dictionaries.txt \
    && test -s dictionaries.txt \
    && ../../../bin/icupkg --ignore-deps -r dictionaries.txt icudt77l-full.dat icudt77l-core.dat \
    && mkdir dictionaries \
    && ../../../bin/icupkg --ignore-deps -x dictionaries.txt -d dictionaries icudt77l.dat \
    && ../../../bin/icupkg --ignore-deps -tl -a dictionaries.txt -s dictionaries new icudt77l-dictionaries.dat \
    && ../../../bin/icupkg --ignore-deps -l icudt77l-core.dat | grep -q 'brkitr/line\.brk' \
    && test "$(../../../bin/icupkg --ignore-deps -l icudt77l-dictionaries.dat | wc -l)" -eq "$(wc -l < dictionaries.txt)" \
    && ls -l icudt77l*.dat


FROM scratch
COPY --from=build /icu/icu-release-77-1/icu4c/source/data/out/tmp/icudt77l-full.dat /icudt.dat
COPY --from=build /icu/icu-release-77-1/icu4c/source/data/out/tmp/icudt77l-core.dat /icudt.core.dat
COPY --from=build /icu/icu-release-77-1/icu4c/source/data/out/tmp/icudt77l-dictionaries.dat /icudt.dictionaries.dat
