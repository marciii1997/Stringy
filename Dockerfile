FROM rust:1.89-bookworm AS build
WORKDIR /build
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo build --locked --release

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 STRINGY_BIN=/usr/local/bin/stringy STRINGY_DATA=/data
RUN groupadd --system stringy && useradd --system --gid stringy stringy && mkdir /data && chown stringy:stringy /data
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=build /build/target/release/stringy /usr/local/bin/stringy
COPY studio ./studio
COPY manage.py .
RUN DJANGO_SECRET_KEY=collectstatic-only python manage.py collectstatic --noinput && chmod -R a+rX /app
USER stringy
CMD ["gunicorn", "studio.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--no-control-socket"]
