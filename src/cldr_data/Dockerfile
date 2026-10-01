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

# icupkg names a package after its output file, and ICU only finds items in a package named icudt77l: each
# package below is written as icudt77l.dat in its own folder. --ignore-deps: the filter excludes char.brk, which
# brkitr/root.res references, and icupkg checks dependencies on every invocation.

# Data the filter keeps for the build tools (genbrk can't initialize ICU without the converter aliases) but
# that text layout never reads: the main locale tree (break iterators use the brkitr tree) and the converter aliases.
RUN ../../../bin/icupkg --ignore-deps -l icudt77l.dat > items.txt \
    && grep -E '^[^/]+\.res$|^cnvalias\.icu$' items.txt > unused.txt \
    && test -s unused.txt \
    && mkdir full \
    && ../../../bin/icupkg --ignore-deps -r unused.txt icudt77l.dat full/icudt77l.dat

# Split the break iterator dictionaries (Chinese/Japanese, Thai, Lao, Khmer, Burmese words: most of the data)
# from the rest, so they can be left out or loaded only when text in those scripts shows up.
RUN grep '\.dict$' items.txt > dictionaries.txt \
    && test -s dictionaries.txt \
    && mkdir core extracted dictionaries \
    && ../../../bin/icupkg --ignore-deps -r dictionaries.txt full/icudt77l.dat core/icudt77l.dat \
    && ../../../bin/icupkg --ignore-deps -x dictionaries.txt -d extracted icudt77l.dat \
    && ../../../bin/icupkg --ignore-deps -tl -a dictionaries.txt -s extracted new dictionaries/icudt77l.dat

# Item names carry the package name (icudt77l/brkitr/line.brk); icupkg -l doesn't show it, so look in the files
RUN grep -q 'icudt77l/brkitr/line\.brk' full/icudt77l.dat \
    && grep -q 'icudt77l/brkitr/thaidict\.dict' full/icudt77l.dat \
    && grep -q 'icudt77l/brkitr/line\.brk' core/icudt77l.dat \
    && ! grep -q 'icudt77l/brkitr/thaidict\.dict' core/icudt77l.dat \
    && grep -q 'icudt77l/brkitr/thaidict\.dict' dictionaries/icudt77l.dat \
    && test "$(../../../bin/icupkg --ignore-deps -l dictionaries/icudt77l.dat | wc -l)" -eq "$(wc -l < dictionaries.txt)" \
    && ls -l full core dictionaries


FROM scratch
COPY --from=build /icu/icu-release-77-1/icu4c/source/data/out/tmp/full/icudt77l.dat /icudt.dat
COPY --from=build /icu/icu-release-77-1/icu4c/source/data/out/tmp/core/icudt77l.dat /icudt.core.dat
COPY --from=build /icu/icu-release-77-1/icu4c/source/data/out/tmp/dictionaries/icudt77l.dat /icudt.dictionaries.dat
