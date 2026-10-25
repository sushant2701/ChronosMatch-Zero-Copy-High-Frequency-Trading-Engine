# AGENT WORKING RULES & OPERATIONAL STANDARDS

**Project:** ChronosMatch - Zero-Copy High-Frequency Trading Engine  
**Internship Organization:** Infotact Solutions  
**Track:** Python Development (Batch-22, Ref: ITS/PY/1090)  
**Intern:** Sushant Shrimal (`sushantshrimal2701@gmail.com`)  
**Active Branch:** `main`  

---

## 1. Git & Version Control Protocol
- **Real Identity:** All commits must be made using the author's real identity (`git config user.name "Sushant Shrimal"`, `git config user.email "sushantshrimal2701@gmail.com"`). Never commit anonymously or with generic agent identities.
- **Session Start:** Always execute `git pull origin <BRANCH>` at the beginning of each working session before making modifications.
- **Push Cadence:** Push commits regularly to the remote branch (`git push origin <BRANCH>`). Never force-push (`git push --force`).
- **Audit Integrity:** NEVER override or manipulate `GIT_AUTHOR_DATE` or `GIT_COMMITTER_DATE`. Never backdate, amend old history, or rewrite commits. All commit timestamps must reflect authentic real-time system clock values.

## 2. Commit Standards & Granularity
- **Daily Rhythm:** Maintain a clean, progressive history of 3 to 5 small commits per day, each representing a single cohesive logical change.
- **Conventional Commits:** Adhere strictly to the Conventional Commits specification:
  - `feat:` for new capabilities or business logic.
  - `fix:` for bug fixes.
  - `test:` for unit, integration, or performance tests.
  - `docs:` for documentation, specifications, and progress logs.
  - `perf:` for latency or memory performance optimizations.
  - `chore:` for build system, dependencies, and configuration updates.
  - `ci:` for CI/CD workflow updates.
- **Meaningful Content:** Every commit must change 50+ characters of substantial code/documentation. Empty or filler commits are strictly forbidden by company internship tracking.
- **Pre-Commit Verification:** Run the test suite (`pytest`) before every commit to guarantee that the main branch remains unbroken.

## 3. Runtime Environment & Portability
- **Platform Support:** Primary development on Windows 11 64-bit; fully portable to Linux and macOS.
- **Python Version:** Python 3.11+ using virtual environments (`venv`), declared in `pyproject.toml` and pinned in `requirements.txt`.
- **Cython / C Compiler Fallback:** Cython matching engine requires a C compiler (MSVC, GCC, or Clang). If a C compiler is unavailable or build fails, automatically fall back to the pure-Python matching engine with an identical interface and log the active backend explicitly.
- **Curses Support:** Use standard `curses` on POSIX systems and `windows-curses` on Windows exclusively.

## 4. Code Quality & Company Training Standards
- **Clean Architecture:** Strict adherence to PEP8 standards, explicit type hints (`typing`), comprehensive docstrings, and descriptive naming conventions (nouns for variables/classes, verbs for functions/methods).
- **Design Principles:** Eliminate magic numbers through typed constants or Enums. Practice DRY (Don't Repeat Yourself), modular functional decomposition, domain-specific custom exceptions, and robust error handling.
- **Tooling & Formatting:** Formatted and linted with `black` and `flake8`.
- **Testing:** Comprehensive `pytest` test suite with fixtures, parameterized tests, and test coverage reporting.
- **Logging:** Structured logging using Python's standard `logging` library; zero unhandled `print` calls in production modules.

## 5. Originality & Academic Integrity
- **Original Codebase:** All application logic must be authored from scratch. Copying source code from external GitHub repositories, online tutorials, or Stack Overflow is strictly prohibited under Infotact Solutions company terms.
- **References:** Record every adapted architectural concept and third-party library along with its license in `docs/REFERENCES.md`.
- **Clarity & Educational Value:** Document the rationale (*"WHY"*) behind every low-latency design decision in plain, accessible English so the intern can explain every file during technical reviews.
- **AI Accountability:** Maintain `docs/AI_USAGE.md` as an honest, transparent record of all AI assistance and code generation. Maintain `docs/CODE_WALKTHROUGH.md` with simple per-module explanations.

## 6. Confidentiality & Synthetic Data
- **Security:** Never store, commit, or reference proprietary company data, API credentials, secrets, or real financial data.
- **Data Generation:** Use strictly synthetic, procedurally generated market data for testing, simulation, and benchmarking.

## 7. Benchmarks & Performance Metrics
- **Real Measurements:** All benchmark figures must be honestly measured on the host hardware using nanosecond timers (`time.perf_counter_ns()`).
- **Reporting:** Save all benchmark results and host hardware specifications in `docs/benchmarks.md`. Never invent, extrapolate, or hardcode figures. If a performance target is missed, report the actual figure transparently with analysis.

## 8. Daily Accountability & Mentor Communication
- **Daily Logs:** At the end of each working day, append a factual, dated entry to `docs/WEEKLY_LOG.md` detailing exact accomplishments, technical challenges, and resolutions.
- **Mentor Updates:** Maintain `docs/MENTOR_UPDATE.md` containing a concise 4-6 line plain-English status summary ready to be sent directly to the corporate mentor.

## 9. Professional Conduct & Transparency
- If a technical requirement is ambiguous, unfeasible, or blocked by environment constraints, explicitly report it as a blocker. Address difficulties proactively rather than obscuring issues.

## 10. Scope Discipline
- Strictly execute ONLY the designated tasks allocated for the current day. Upon completion, stop and output:
  1. List of commits made.
  2. Test execution results.
  3. Working features and verified capabilities.
  4. Blockers or items pending next session.
