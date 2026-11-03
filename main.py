from __future__ import annotations

import argparse

from hoster import Database, Settings, build_application, configure_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="LOFI GWD Multi-Userbot Hoster")
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="validate configuration, create runtime directories, and initialize SQLite",
    )
    args = parser.parse_args()

    try:
        settings = Settings.from_env()
    except ValueError as exc:
        parser.error(str(exc))
    Database(settings.database_path, settings.owner_id)
    if args.check_config:
        print("Configuration, runtime directories, and SQLite schema are valid.")
        return

    configure_logging(settings)
    application = build_application(settings)
    application.run_polling(allowed_updates=None)


if __name__ == "__main__":
    main()
