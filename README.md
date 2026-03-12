# http-code-checker

## Install

The usual steps of a django install.

### Clone

Add deploy key to the repo and clone it 

```
git@github.com:layershift/http-code-checker.git
```

### Configure env file

Based on .env.sample

### Additional requirements

- redis/valky
- postgres for database.

Both of these can be local or remote

## Running

### Manually

#### Server

```
uv run  manage.py runserver 0.0.0.0:8000
```

#### Rq workers

```
 uv run manage.py rqworker  monitoring default high low --with-scheduler
```

### As a service

```
Service files will go here once we have more
```


## Add/remove domains/servers/triggers

From the server that needs monitoring:

```
curl -s https://dontdeletezoltan.man-1.solus.stage.town/api/v1/bash/handler.sh  | bash -s --
Usage: bash [OPTION] [ARGUMENT]
Options:
  --add-server                       Add server using hostname
  --add-domain DOMAIN                Add specific domain
  --add-all-domains                  Add all domains from Plesk
  --make-snapshot DOMAIN             Create snapshot for specific domain
  --make-baseline-snapshot DOMAIN    Create snapshot for specific domain and set as baseline
  --make-all-snapshots               Create snapshots for all domains
  --make-all-baseline-snapshots      Create snapshots for all domains and set as baseline
  --report DOMAIN                    Generate report for specific domain
  --report-all                       Generate server report
  --help                             Show this help message

```

More details will be described in insight once the final version is prroved, agreed on.

