# Bereg implementation continuation

Goal: public multi-visitor rental shortlist using weekly multi-place transit journeys and house evidence.
Baseline c28b53a, PR38 merged in cb68687; existing local Forecast checkout preserved in ../farposton.
Active branch feature/bereg-complete, worktree ../bereg-complete.

Implemented: transactional house importer/read model, weekly multi-place routing jobs, grouped house map, frontend, public sessions/CSRF. Full suite410 tests passed; independent UI Chrome scenario uses offline routing fixture, no JS errors or horizontal overflow1440/390. Candidate container/real public route verification next.
2GIS: bounded live routing probe on vibe1 returned a transport route; Places HTTP200 contains meta403 with current key. No real API responses persisted. Quota/retention permissions not confirmed; jobs remain memory-only, explicit request budgets.
Production read-only inspection: farposton.renosaza.com -> 127.0.0.1:5000 -> flats-preview container, older source a697ee5; separate old Forecast on8445. Existing flats volume must be backed up and preserved. New candidate uses5001 and separate copied data volume before switch.
Next: finish integrations, run tests, browser scenario, candidate release, public verification, publish branch/PR.

Release: new image uses1 Gunicorn worker8threads, persistent SQLite and API quota ledger. Existing803 photo vectors retained; new model training not required. Public Places API unavailable meta403; pin confirmation is available.
