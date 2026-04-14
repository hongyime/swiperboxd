Product Requirements Document: Media Discovery PWA

Version: 1.1.0 | Status: Approved for Implementation

1. Executive Summary

The Media Discovery PWA is a serverless, discovery-driven engine that transforms static movie lists into an interactive, swipe-based decision loop. It solves "choice paralysis" by programmatically filtering out content the user has already seen or queued, presenting only unseen titles. By caching discovered metadata globally, the platform accelerates discovery for subsequent users while minimizing target platform scraping.

2. User Personas & Use Cases

The Power User: Has thousands of logged films. Needs an initial background sync to filter out their extensive history so they only see fresh recommendations.

The Causal Discoverer: Uses predefined deck profiles (e.g., "High Rating / Low Popularity") to quickly find hidden gems and funnel them into a watchlist.

3. Technical Stack

Frontend: Next.js (React), Tailwind CSS, Zustand (State Management), PWA Manifest.

Backend: Vercel Serverless Functions (Python/FastAPI via @vercel/python).

Database: Supabase (PostgreSQL) with Supavisor connection pooling.

Cache & Queuing: Upstash Redis (Session/Rate Limiting) and Upstash QStash (Asynchronous task queueing).

4. Functional Requirements

4.1 Authentication & Session Management

4.1.1 The system shall authenticate users by proxying credentials to the target platform and retrieving the session cookie.

4.1.2 The system shall encrypt the session cookie using an AES-256 master key and store it locally for the user.

4.2 Data Ingestion & State

4.2.1 On initial login, the system shall queue an asynchronous task to ingest the user's historical diary and store the slugs in a Supabase user_exclusions table.

4.2.2 The frontend shall display a real-time progress indicator during the initial background ingestion.

4.2.3 The system shall maintain a 24-hour localized not_interested list via browser LocalStorage for skipped items.

4.3 The Discovery Engine

4.3.1 The system shall provide predefined filtering profiles (e.g., "The Gold Standard", "Hidden Gems").

4.3.2 The backend shall scrape the target platform page-by-page, cross-referencing against the user_exclusions table before transmitting the payload to the client.

4.3.3 The system shall upsert discovered film metadata into a global movies table to serve as a Cache-Aside database.

4.3.4 Records with malformed or missing critical data (e.g., poster URLs) shall trigger an exponential backoff retry; if unresolvable, the record is dropped.

4.4 Interaction & UI

4.4.1 The client shall implement a gesture-based UI: Swipe Right (Watchlist), Swipe Left (Dismiss), Swipe Up (Log).

4.4.2 The client shall enforce a 500ms UI lock (isSyncing) post-interaction to prevent API rate-limit breaches.

4.4.3 Images shall be served using a network-first caching strategy via browser cache headers to optimize load times.

5. Non-Functional Requirements

Performance: Card decks must initialize within 1.5 seconds. Cached metadata queries must return in <50ms.

Scalability: The architecture must gracefully handle Vercel's free-tier limitations via asynchronous offloading and connection pooling.

Security: Master encryption keys are managed via Vercel Environment Variables. Target platform passwords are strictly pass-through and never stored.

Resilience: The backend scraper must implement a Rotating Proxy Fallback upon encountering 429 or 403 HTTP status codes.

6. System Architecture Flow

Client Request: User selects a deck profile.

Cache Check: Vercel Function checks Supabase movies table for indexed metadata.

Target Sync (if miss): Function dynamically scrapes the target platform, upserts new metadata to Supabase, and updates Upstash Redis rate limits.

Exclusion Filter: Results are filtered against the user's Supabase user_exclusions.

Delivery: The clean queue is pushed to the client's Zustand store for rendering. Actions (swipes) trigger asynchronous updates back to the target platform and local tables.

7. Success Metrics

System Stability: <1% rate-limit blockage from the target platform.

Cache Efficiency: 80%+ cache hit rate on the movies table after the first month of operation.

Engagement: High volume of swipe-to-watchlist conversions per session.

8. Infrastructure Operations & Maintenance (Keep-Alive Strategy)

To ensure the Supabase free-tier project remains active and avoids being paused due to the 7-day inactivity policy, the system will utilize a scheduled GitHub Action to execute a lightweight database ping. This automates maintenance and ensures uninterrupted availability.

8.1 GitHub Actions Workflow (supabase-keep-alive.yml)

Create a file at .github/workflows/keep-alive.yml in the repository with the following configuration:

name: Supabase Keep-Alive
on:
  schedule:
    # Runs at 00:00 UTC every Monday and Thursday
    - cron: '0 0 * * 1,4'
  workflow_dispatch: # Allows manual triggering for testing
jobs:
  ping-db:
    runs-on: ubuntu-latest
    steps:
      - name: Ping Supabase Database
        run: |
          curl -X POST "${{ secrets.SUPABASE_URL }}/rest/v1/rpc/keep_alive_ping" \
          -H "apikey: ${{ secrets.SUPABASE_ANON_KEY }}" \
          -H "Authorization: Bearer ${{ secrets.SUPABASE_ANON_KEY }}" \
          -H "Content-Type: application/json"


8.2 Supabase Remote Procedure Call (RPC) Implementation

To allow the secure execution of the curl command without exposing table data or requiring heavy database driver installations, a lightweight function must be instantiated in the Supabase SQL Editor:

create or replace function keep_alive_ping()
returns void as $$
begin
  -- This function executes a minimal operation to register activity and wake the DB
end;
$$ language plpgsql security definer;


8.3 Environment Secrets configuration

The following variables must be defined in the GitHub Repository Settings (Settings > Secrets and variables > Actions):

SUPABASE_URL: The project URL (e.g., https://xyz.supabase.co).

SUPABASE_ANON_KEY: The project’s anonymous API key.

By scheduling this workflow twice a week, the system consistently satisfies Supabase's activity requirements, resetting the inactivity timer programmatically.
