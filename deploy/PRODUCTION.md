# DbOps production deployment

## Build and configure

Deploy this checkout to a dedicated Linux service account. Create `deploy/dbops.env` from `dbops.env.example`, set it to mode `600`, create the data directory, then build and start:

```bash
cd /opt/dbops/open-webui
cp deploy/dbops.env.example deploy/dbops.env
chmod 600 deploy/dbops.env
mkdir -p /opt/dbops/data
./deploy/dbopsctl.sh build
./deploy/dbopsctl.sh start
./deploy/dbopsctl.sh test
```

The production process serves the compiled `build/` frontend and API from one port. Put NGINX, an F5, or another TLS reverse proxy in front of `127.0.0.1:8080`; terminate HTTPS there and do not expose the service port directly.

## Lifecycle

```bash
./deploy/dbopsctl.sh status
./deploy/dbopsctl.sh stop
./deploy/dbopsctl.sh restart
tail -f /opt/dbops/data/runtime/dbops.log
```

## Verification

- `./deploy/dbopsctl.sh test` reports a successful health check.
- Sign in through the HTTPS proxy and run one read-only monitoring card.
- Confirm the local SQLite bootstrap database remains writable and the Oracle 23ai vector connection, once configured in DbOps, is reachable.

## Rollback

Stop the new process, restore the prior checkout and its matching `build/` output, retain `/opt/dbops/data`, then start and test. Do not delete the data directory during rollback.
