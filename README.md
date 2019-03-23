<p align="center">
  <img src="https://shipchain.io/img/logo.png" alt="ShipChain"/>
</p>


[![License](http://img.shields.io/:license-apache-blue.svg)](http://www.apache.org/licenses/LICENSE-2.0.html)
[![Chat](https://img.shields.io/badge/gitter-ShipChain/lobby-green.svg)](https://gitter.im/ShipChain/Lobby)

# Hydra manages many heads of networks

## Get-started guide

### Install
``` 
make virtualenv
source env/bin/activate
```
### Configure client and run node

Note: Replace network-name with your chosen network (e.g. shipchain-testnet-alpha)
1. Join a network
`hydra client join-network -n network-name`
2. Configure client
`hydra client configure -n network-name`
3. Run node
`cd network-name && ./shipchain run`


## Installation

```
$ pip install -r requirements.txt

$ pip install setup.py
```

## Development

This project includes a number of helpers in the `Makefile` to streamline common development tasks.

### Environment Setup

The following demonstrates setting up and working with a development environment:

```
### create a virtualenv for development

$ make virtualenv

$ source env/bin/activate


### run hydra cli application

$ hydra --help


### run pytest / coverage

$ make test
```


### Releasing to PyPi

Before releasing to PyPi, you must configure your login credentials:

**~/.pypirc**:

```
[pypi]
username = YOUR_USERNAME
password = YOUR_PASSWORD
```

Then use the included helper function via the `Makefile`:

```
$ make dist

$ make dist-upload
```

## Deployments

### Docker

Included is a basic `Dockerfile` for building and distributing `ShipChain Network Hydra Manager`,
and can be built with the included `make` helper:

```
$ make docker

$ docker run -it hydra --help
```