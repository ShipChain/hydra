FROM python:3.6-alpine3.9
MAINTAINER Lee Bailey <lbailey@shipchain.io>
ENV PS1="\[\e[0;33m\]|> hydra <| \[\e[1;35m\]\W\[\e[0m\] \[\e[0m\]# "

RUN apk add --no-cache build-base libffi-dev openssl-dev

WORKDIR /src
COPY . /src
RUN pip install --no-cache-dir -r requirements.txt \
    && python setup.py install
WORKDIR /
ENTRYPOINT ["hydra"]
