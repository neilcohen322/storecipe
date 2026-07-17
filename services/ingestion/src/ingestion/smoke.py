from ingestion.worker import smoke


def main() -> None:
    result = smoke.delay()
    print(result.get(timeout=10))


if __name__ == "__main__":
    main()
