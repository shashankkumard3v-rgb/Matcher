# Project Context (SAMPLE for the fictional Aarav Mehta - for testing only)

## Profile
- Languages:
- Other facts:
  - AWS Certified Developer - Associate, 2023
  - B.E. Computer Engineering, Savitribai Phule Pune University, 2019
  - LeetCode: 450+ problems solved, contest rating 1820
- Links:
  - https://leetcode.com/u/aarav-sample
  - https://www.linkedin.com/in/aarav-mehta-sample
  - https://github.com/aarav-sample

## Project: Settlement events pipeline
- Employer: Finlytics Pvt Ltd
- Role: Software Engineer II
- Start: 2023-02
- End: 2023-09
- Team size: 4
- Problem: Settlement status updates came from a nightly batch, so merchants saw stale payout status for up to a day.
- What I did:
  - Built a Kafka consumer service in Python that processed settlement events in real time
  - Added retry and dead-letter handling for failed events
  - Deployed the consumers on Kubernetes (EKS) with Helm charts
  - Added Grafana dashboards and Prometheus alerts for consumer lag
- Tech:
  - Python
  - Kafka
  - Kubernetes
  - PostgreSQL
  - Grafana
- Results:
  - Payout status delay went from up to 24 hours to under 1 minute
  - Handled about 2 million events per day at peak

## Project: Payouts API latency work
- Employer: Finlytics Pvt Ltd
- Role: Software Engineer II
- Start: 2022-05
- End: 2022-08
- Team size: 2
- Problem: The payouts API p99 latency was over 2 seconds during month-end peaks.
- What I did:
  - Profiled slow endpoints and added Redis caching for merchant config lookups
  - Rewrote two N+1 queries in the payouts listing endpoint
  - Added Grafana dashboards and alerts for API latency
- Tech:
  - FastAPI
  - Redis
  - PostgreSQL
  - Grafana
- Results:
  - p99 latency dropped from 2.1s to 450ms during month-end peaks

## Project: Bank reconciliation job
- Employer: Finlytics Pvt Ltd
- Role: Software Engineer II
- Start: 2021-10
- End: 2022-03
- Team size: 3
- Problem: The finance team matched bank statements with settlement records by hand in spreadsheets every day.
- What I did:
  - Built a daily Celery job that parses bank statement files and matches them to settlement records
  - Flagged mismatches in a review screen for the finance team
  - Added an append-only audit log table for payout changes with the compliance team
  - Wrote pytest unit and integration tests for the matching logic
- Tech:
  - Python
  - Celery
  - PostgreSQL
  - AWS S3
  - pytest
- Results:
  - Manual reconciliation work went from about 3 hours a day to 20 minutes
  - Test coverage of the reconciliation module went from 45% to 82%

## Project: Shipping partner integrations
- Employer: CartNest
- Role: Software Engineer
- Start: 2020-01
- End: 2020-09
- Team size: 3
- Problem: Order tracking broke whenever a shipping partner's webhook failed or arrived twice.
- What I did:
  - Integrated 3 shipping partner APIs into the Django order service
  - Built webhook handling with retries and idempotency keys
  - Exposed order tracking REST APIs for the web and Android apps
- Tech:
  - Python
  - Django
  - PostgreSQL
  - Redis
- Results:
  - Supported about 20,000 orders per day
  - Duplicate shipment updates stopped after idempotency keys were added
