#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
repo_root=$PWD
temp=$(mktemp -d)
trap 'rm -rf -- "$temp"' EXIT

import_patch() {
  local name=$1 base=$2 fork=$3
  shift 3
  local checkout="$temp/$name"
  git init --quiet "$checkout"
  git -C "$checkout" fetch --quiet --depth=1 "https://github.com/hiddify/$name.git" "$base"
  git -C "$checkout" fetch --quiet --depth=1 "https://github.com/SawaMEN/$name.git" "$fork"
  git -C "$checkout" diff --binary --full-index --no-renames "$base" "$fork" -- . \
    ':(exclude).github' ':(exclude)AGENTS.md' "$@" > "$repo_root/patches/$name/0001-fork-baseline.patch"
  find "$repo_root/patches/$name" -maxdepth 1 -name '*.patch' ! -name '0001-fork-baseline.patch' ! -name '0002-vetroff-version.patch' -delete
}

import_patch hiddify-core b097daadd89d2b62e30c0f4353f55a973118514c \
  a5662c60c181090d860f165bffe9734c896bae23 \
  ':(exclude).gitmodules' ':(exclude)hiddify-sing-box' ':(exclude)ray2sing' ':(exclude)hiddify-core.tar.gz'
import_patch hiddify-sing-box 004371d6be2395ee4379054a8cf113f339dbe231 \
  45814844d5b80af5b43ac5c17d45c0ed3a42bcd0
import_patch ray2sing cddb962a5a9d02f4136353e76347e7d909b12e65 \
  7aac8dcf42b6284cf6993c992db8dcf259a4b98f
