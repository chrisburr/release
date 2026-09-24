#!/bin/sh
# Ask conda-forge to open a version update pull request on a feedstock, proving
# which CI job is asking with the identity token its provider issued it.
#
#   trusted_publish.sh FEEDSTOCK VERSION [FEEDSTOCK_BRANCH]
#
# CF_RELEASE_ID_TOKEN      the job's identity token, for audience conda-forge-updater
# CF_RELEASE_WEBSERVICES   where conda-forge-webservices runs
# CF_RELEASE_OUTPUT_FILE   if set, pull-request-number=N is appended to it
#
# It needs nothing but sh and curl, so that it can run in a job that installs
# nothing before it: whatever runs earlier in a job can read the token.
set -eu

feedstock=$1
version=$2
branch=${3:-}
webservices=${CF_RELEASE_WEBSERVICES:-https://services.conda-forge.org}

if [ -z "${CF_RELEASE_ID_TOKEN:-}" ]; then
    echo "CF_RELEASE_ID_TOKEN is not set, so there is no identity token to send" >&2
    exit 1
fi

# The same patterns the endpoint applies, checked here so that the values can
# go into the json body as they are: none of them can hold a quote.
check() {
    if [ "$(printf '%s' "$2" | wc -l)" -ne 0 ] \
        || ! printf '%s' "$2" | grep -Eq "$3"; then
        echo "the $1 '$2' is not one conda-forge will accept" >&2
        exit 1
    fi
}
check feedstock "$feedstock" '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}-feedstock$'
check version "$version" '^[A-Za-z0-9][A-Za-z0-9._+!]{0,63}$'

body="{\"feedstock\": \"$feedstock\", \"version\": \"$version\""
if [ -n "$branch" ]; then
    check branch "$branch" '^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$'
    body="$body, \"branch\": \"$branch\""
fi
body="$body}"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# from a file, so that the token is not on curl's command line for ps to show
umask 077
printf 'Authorization: Bearer %s\n' "$CF_RELEASE_ID_TOKEN" > "$work/auth"

attempt=1
while :; do
    status=$(curl --silent --show-error --max-time 60 \
        --request POST \
        --header "@$work/auth" \
        --header 'Content-Type: application/json' \
        --data "$body" \
        --dump-header "$work/headers" \
        --output "$work/response" \
        --write-out '%{http_code}' \
        "$webservices/trusted-publishing/version-update") || status=000

    # conda-forge has not loaded its keys yet, or is fetching a rotated one;
    # the token is not spent, so the same one can be sent again
    if [ "$status" = 503 ] && [ "$attempt" -lt 5 ]; then
        wait=$(sed -n 's/^[Rr]etry-[Aa]fter: *\([0-9][0-9]*\).*/\1/p' "$work/headers")
        echo "conda-forge asked us to retry in ${wait:-15}s" >&2
        sleep "${wait:-15}"
        attempt=$((attempt + 1))
        continue
    fi
    break
done

if [ "$status" != 202 ]; then
    echo "conda-forge did not open a pull request (HTTP $status):" >&2
    cat "$work/response" >&2
    echo >&2
    exit 1
fi

number=$(sed -n 's/.*"pull_request": *\([0-9][0-9]*\).*/\1/p' "$work/response")
url=$(sed -n 's/.*"url": *"\([^"]*\)".*/\1/p' "$work/response")

if [ -n "${CF_RELEASE_OUTPUT_FILE:-}" ]; then
    echo "pull-request-number=$number" >> "$CF_RELEASE_OUTPUT_FILE"
fi

# the pull request exists, so asking again would only open another
if grep -q '"version_update_started": *false' "$work/response"; then
    echo "conda-forge opened $url but could not start updating it to $version;" \
        "close it and run this job again, or ping conda-forge/core" >&2
    exit 1
fi
echo "conda-forge opened $url and is updating it to $version"
