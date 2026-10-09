ARG BUILD_FROM
FROM $BUILD_FROM

# Pure Python on purpose: the analysis is a handful of small least-squares fits,
# so there is no numpy to build on a Pi. tzdata gives zoneinfo the time zones.
# websocket-client and paho-mqtt are pure Python and come from pip.
RUN apk add --no-cache python3 py3-pip tzdata \
    && pip3 install --no-cache-dir --break-system-packages "websocket-client>=1.6,<2" "paho-mqtt>=1.6,<3"

COPY analysis.py maverick.py ui.html /app/
COPY run.sh /run.sh
RUN chmod a+x /run.sh

LABEL io.hass.type="addon"

CMD ["/run.sh"]
