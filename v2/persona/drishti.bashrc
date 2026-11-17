# Source this from ~/.bashrc:  source /mnt/c/Users/abhij/Documents/Drishti/v2/persona/drishti.bashrc
# Usage:  drishti                  starts Drishti at your last level (investor the first time)
#         drishti starter          pick a level: starter, learner, investor, pro, coach (remembered for next time)
#         drishti pro --model sonnet         anything after the level goes to claude
#         DRISHTI_GREET=0 drishti            no opening line (it is a fixed line, with no model call and no tokens)
unalias drishti 2>/dev/null
drishti() {
  local d=/mnt/c/Users/abhij/Documents/Drishti/v2/persona f="$HOME/.drishti-level" v
  v=$(cat "$f" 2>/dev/null); v=${v:-investor}
  if [ $# -gt 0 ] && [[ "$1" != -* ]]; then v=$1; shift; fi
  if [ ! -f "$d/levels/$v.md" ]; then
    echo "levels: $(ls "$d/levels" | sed 's/\.md$//' | tr '\n' ' ')" >&2
    return 1
  fi
  echo "$v" > "$f"
  if [ "${DRISHTI_GREET:-1}" != "0" ] && [ -f "$d/greetings.conf" ]; then
    local h part g; h=$(date +%H); part=evening
    [ "$h" -lt 17 ] && part=afternoon
    [ "$h" -lt 12 ] && part=morning
    g=$(grep "^$v=" "$d/greetings.conf" | cut -d= -f2- | shuf -n1 | sed "s/{part}/$part/g; s/{name}/${DRISHTI_NAME:-there}/g")
    [ -n "$g" ] && printf '\n  %s\n\n' "$g"
  fi
  claude-personal --append-system-prompt "$(cat "$d/core.md" "$d/levels/$v.md")" "$@"
}
