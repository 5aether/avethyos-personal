# The workflow supplies a verified, successful AvethyOS release by digest.
ARG BASE_IMAGE
FROM scratch AS customization
COPY packages.txt remove-packages.txt flatpak-packages.txt remove-flatpak-packages.txt kernel-arguments.toml build.sh customize.sh /custom/
COPY scripts/flatpaks.py /custom/
COPY files /files

# CI verifies actual extra-data installation and first-boot defaults, even when
# the user's package lists are empty. This test stage is not part of the image.
FROM ${BASE_IMAGE} AS flatpak-check
COPY scripts/flatpaks.py /checks/scripts/flatpaks.py
COPY tests/test_flatpak_extra_data.py /checks/tests/test_flatpak_extra_data.py
RUN command -v cc && command -v gpg && command -v ostree && command -v flatpak && \
    python3 -B -m unittest discover -s /checks/tests -v

FROM ${BASE_IMAGE}
# Add one small customization layer and keep all inherited AvethyOS layers.
RUN --mount=type=bind,from=customization,source=/custom,target=/custom \
    --mount=type=bind,from=customization,source=/files,target=/files \
    bash /custom/customize.sh
