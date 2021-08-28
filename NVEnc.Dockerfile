# Install arib-subtitle-timedmetadater
FROM node:buster
RUN mkdir -p /app/server/thirdparty/arib-subtitle-timedmetadater
RUN npm install -g arib-subtitle-timedmetadater
RUN ln -s /usr/local/bin/arib-subtitle-timedmetadater /app/server/thirdparty/arib-subtitle-timedmetadater/arib-subtitle-timedmetadater.elf

FROM python:latest
ADD ./ /app
WORKDIR /app/server
ENV PIPENV_VENV_IN_PROJECT true

# Install dependencies
RUN pip install pipenv
RUN pipenv install

# Upgrade database
RUN pipenv run aerich upgrade

# Install NVIDIA Web Driver
RUN apt update
RUN apt -y install software-properties-common
RUN add-apt-repository -y ppa:graphics-drivers/ppa
RUN apt update
RUN apt -y install nvidia-driver-430

# Install NVEncC
RUN apt -y update; apt -y upgrade
RUN apt -y install libavcodec-dev libavutil-dev libavformat-dev libswresample-dev libavfilter-dev libass-dev
RUN wget https://github.com/rigaya/NVEnc/releases/download/5.37/nvencc_5.37_Ubuntu20.04_amd64.deb
RUN apt -y install ./nvencc_5.37_Ubuntu20.04_amd64.deb

ENTRYPOINT ["pipenv", "run", "serve"]
