FROM harbor.lab.test/dockerhub/library/python:3.12-slim AS build
WORKDIR /src
COPY app.py .

FROM harbor.lab.test/dockerhub/library/python:3.12-slim
RUN useradd --uid 65532 --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=build /src/app.py /app/app.py
USER 65532
EXPOSE 8080
CMD ["python3", "-u", "/app/app.py"]
