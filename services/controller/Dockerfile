FROM harbor.lab.test/dockerhub/library/python:3.12-slim AS build
WORKDIR /src
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt
COPY controller.py .

FROM harbor.lab.test/dockerhub/library/python:3.12-slim
RUN useradd --uid 65532 --no-create-home --shell /usr/sbin/nologin app
COPY --from=build /install /usr/local
COPY --from=build /src/controller.py /app/controller.py
USER 65532
CMD ["python3", "-u", "/app/controller.py"]
