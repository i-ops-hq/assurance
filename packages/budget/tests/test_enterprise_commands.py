"""The test and check commands enterprise stacks type, recognised as what they are.

Found by probing what a Java bank, a Django shop or a monorepo team runs every day (2026-10-13):
`./mvnw test`, `./gradlew check`, `bazel test`, `npm run test:unit`, `python manage.py test`, `nx
run-many -t test` and the security scanners all came back unclassified, so on those stacks every
notice ended in "could not classify" and the hook could say nothing about whether the tests passed.
Build tools take their goals and tasks anywhere among their flags, so they are read, not prefixed.
"""

from __future__ import annotations

import pytest

from assurance_budget.sessions import classify_bash


@pytest.mark.parametrize("command, kind", [
    # Maven and Gradle, wrapper scripts and flags anywhere
    ("./mvnw test", "test"),
    ("mvn -q -pl api -am verify", "test"),
    ("mvn clean install", "test"),  # install runs the tests on the way
    ("mvn -DskipTests package", "check"),  # it compiled; it tested nothing
    ("mvn -Dmaven.test.skip=true install", "check"),
    ("mvn compile", "check"),
    ("mvn spotless:check", "check"),
    ("mvn clean", "unclassified"),
    ("./gradlew test", "test"),
    ("./gradlew :app:testDebugUnitTest", "test"),
    ("gradle --console plain check", "test"),
    ("./gradlew build -x test", "check"),
    ("./gradlew compileJava", "check"),
    ("./gradlew clean", "unclassified"),
    ("sbt test", "test"),
    ('sbt "testOnly com.example.FooSpec"', "test"),
    ("sbt compile", "check"),
    ("bazel test //...", "test"),
    ("bazelisk --bazelrc=ci.rc build //app", "check"),
    # Monorepo runners
    ("npx nx run-many -t test lint", "test"),
    ("nx affected --target=lint", "check"),
    ("nx run api:test", "test"),
    ("turbo run test", "test"),
    ("lerna run lint", "check"),
    # Package scripts by name, and binaries a package manager runs
    ("npm run test:unit", "test"),
    ("pnpm test:e2e", "test"),
    ("yarn lint:ci", "check"),
    ("yarn jest --ci", "test"),
    ("pnpm vitest run", "test"),
    ("npm run build", "unclassified"),
    ("npm run lint:fix", "unclassified"),  # a fixer writes files; it checks nothing
    ("npm run format", "unclassified"),
    ("npm run format:check", "check"),
    ("npm install", "write"),
    # make and just targets by name
    ("make test-unit", "test"),
    ("make -j4 test", "test"),
    ("make lint", "check"),
    ("make lint-fix", "unclassified"),
    ("just test", "test"),
    # Frameworks and platforms
    ("python manage.py test", "test"),
    ("bundle exec rspec spec/models", "test"),
    ("bundle exec rake test", "test"),
    ("rails test", "test"),
    ("php artisan test", "test"),
    ("./vendor/bin/pest", "test"),
    ("composer test", "test"),
    ("mix test", "test"),
    ("flutter test", "test"),
    ("xcodebuild -scheme App -destination 'platform=iOS Simulator' test", "test"),
    ("cargo nextest run", "test"),
    ("npx playwright test", "test"),
    ("npx cypress run", "test"),
    ("coverage run -m pytest", "test"),
    ("cargo check", "check"),
    ("dotnet build", "check"),
    # Security scanners and infrastructure checks
    ("semgrep scan --config auto", "check"),
    ("bandit -r src", "check"),
    ("trivy fs .", "check"),
    ("pip-audit", "check"),
    ("npm audit --audit-level=high", "check"),
    ("gitleaks detect", "check"),
    ("checkov -d infra", "check"),
    ("hadolint Dockerfile", "check"),
    ("terraform validate", "check"),
    ("terraform plan -out plan.bin", "check"),
    ("terraform apply", "unclassified"),  # a change, not a check: the hook calls it a deploy
    ("helm lint chart", "check"),
    ("kubectl diff -f k8s/", "check"),
    # Found in review (2026-10-13): what prints instead of running is a read
    ("make -n test", "read"),
    ("make --dry-run test", "read"),
    ("just --show test", "read"),
    ("./gradlew test --dry-run", "read"),
    ("turbo run test --dry-run", "read"),
    ("xcodebuild -showBuildSettings -scheme App test", "read"),
    # what ships is not a test, whatever its name: its failure may be the upload's
    ("make ci-deploy", "unclassified"),
    ("npm run ci:publish", "unclassified"),
    ("just check-and-deploy", "unclassified"),
    ("mvn clean deploy", "unclassified"),
    ("./gradlew build publish", "unclassified"),
    # compiling the tests runs none of them
    ("./gradlew testClasses", "unclassified"),
    ("./gradlew assembleAndroidTest", "unclassified"),
    # `check` is a check for a package script, and the test suite for make (GNU's convention)
    ("npm run check", "check"),
    ("make check", "test"),
    # a checker told to fix rewrites the files it checks
    ("rubocop -a", "write"),
    ("biome check --write", "write"),
    ("eslint --fix .", "write"),
    ("ruff check --fix .", "write"),
    ("ktlint -F", "write"),
    ("semgrep --autofix", "write"),
])
def test_an_enterprise_command_is_what_it_runs(command: str, kind: str) -> None:
    assert classify_bash(command) == kind
