FROM python:3.12-slim

WORKDIR /srv

COPY app ./app

ENV PORT=8080
EXPOSE 8080

CMD ["python3", "-m", "app.server"]
