FROM python:latest
ADD ./ /app
WORKDIR /app/server
ENV PIPENV_VENV_IN_PROJECT true

# Install dependencies
RUN pip install pipenv
RUN pipenv install

# Upgrade database
RUN pipenv run aerich upgrade

# Install FFmpeg
RUN apt -y update; apt -y upgrade; apt -y install ffmpeg
RUN mkdir -p /app/server/thirdparty/FFmpeg
RUN ln -s /usr/bin/ffmpeg /app/server/thirdparty/FFmpeg/ffmpeg.elf

ENTRYPOINT ["pipenv", "run", "serve"]
