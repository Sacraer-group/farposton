# Bereg implementation continuation

Goal: public multi-visitor rental shortlist using weekly multi-place transit journeys and house evidence.
Baseline c28b53a, PR38 merged in cb68687; existing local Forecast checkout preserved in ../farposton.
Active branch feature/bereg-complete, worktree ../bereg-complete.

Implemented: transactional house importer/read model, weekly multi-place routing jobs, grouped house map, frontend, public sessions/CSRF. Full suite410 tests passed; independent UI Chrome scenario uses offline routing fixture, no JS errors or horizontal overflow1440/390. Public release8983ee9 completed via CI43532864183. Four actual public routing results/cancel, two browser isolation and restart preservation verified; full weekly shortlist calculation remains a judging-readiness task.
2GIS: bounded live routing probe on vibe1 returned a transport route; Places HTTP200 contains meta403 with current key. No real API responses persisted. Quota/retention permissions not confirmed; jobs remain memory-only, explicit request budgets.
Production read-only inspection: farposton.renosaza.com -> 127.0.0.1:5000 -> flats-preview container, older source a697ee5; separate old Forecast on8445. Existing flats volume must be backed up and preserved. New candidate uses5001 and separate copied data volume before switch.
Next: judging-readiness: explicit quota-safe shortlist, full live weekly demo, evidence matrix, user validation protocol and defense. Actual deployed upstream is staged Bereg container, backed by its own copied SQLite volume; prior preview remains available for rollback.

Release: new image uses1 Gunicorn worker8threads, persistent SQLite and API quota ledger. Existing803 photo vectors retained; new model training not required. Public Places API unavailable meta403; pin confirmation is available.

Judging-readiness follow-up: new scoped saved-candidate calculation and non-mutating quota estimate;414tests passed. Real-provider check on isolated serverDB copy:5places6legs5weekdays3flats ->90/90ok,50uniquequeries. Detailed rubric/source verification, defense script, AI evidence and unfilled real-user protocol in docs/judging. No user-study outcome claimed.
