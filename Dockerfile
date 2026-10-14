FROM python:3.12-slim

WORKDIR /app

COPY . /app

RUN pip install --no-cache-dir .

VOLUME ["/data"]

ENV AUTOPOSTER_DATA_DIR=/data

ENTRYPOINT ["autoposter"]
CMD ["run"]
