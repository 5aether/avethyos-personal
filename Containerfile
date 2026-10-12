# The workflow supplies a verified, successful AvethyOS release by digest.
ARG BASE_IMAGE
FROM scratch AS customization
COPY packages.txt remove-packages.txt kernel-arguments.toml build.sh customize.sh /custom/
COPY files /files

FROM ${BASE_IMAGE}
# Add one small customization layer and keep all inherited AvethyOS layers.
RUN --mount=type=bind,from=customization,source=/custom,target=/custom \
    --mount=type=bind,from=customization,source=/files,target=/files \
    bash /custom/customize.sh
