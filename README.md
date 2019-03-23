<p align="center">
  <img src="https://shipchain.io/img/logo.png" alt="ShipChain"/>
</p>


[![License](http://img.shields.io/:license-apache-blue.svg)](http://www.apache.org/licenses/LICENSE-2.0.html)
[![Chat](https://img.shields.io/badge/gitter-ShipChain/lobby-green.svg)](https://gitter.im/ShipChain/Lobby)

# Hydra manages many heads of networks

## Development

This project includes a number of helpers in the `Makefile` to streamline common development tasks.

## Installation

$ make virtualenv

$ source env/bin/activate

## Usage

### Client controller
#### Configure client
```
$ hydra client configure *network name*
```
Takes network name as a parameter
#### Join a network
```
$ hydra client join-network *network name*
```
Takes network name as a paramenter (e.g. shipchain-testnet-alpha)

#### Update hydra 
````
$ hydra client update
````

#### 
```
$ hydra set-channel
```

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
