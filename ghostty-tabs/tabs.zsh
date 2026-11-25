# Ghostty tab tracking. Sourced from zshrc.
# A tab shell keeps live/<pid>.json up to date for `ghostty-tabs snapshot`.
# A tab that `ghostty-tabs restore` opened acts once at its first prompt.
# Starts no subprocess at a prompt: the file commands are zsh/files builtins.

[[ -o interactive ]] || return 0

# GHOSTTY_TABS_PROC_ROOT is a test seam: the directory that holds the process
# files of the parent chain. It is /proc in normal use.
_ghostty_tabs_is_tab_shell() {
  emulate -L zsh
  local root=${GHOSTTY_TABS_PROC_ROOT:-/proc} p=$PPID comm cmd stat
  local -a f
  [[ -r $root/$p/comm ]] || return 1
  comm=$(<$root/$p/comm)
  [[ $comm == ghostty ]] && return 0
  [[ $comm == sh && -r $root/$p/cmdline && -r $root/$p/stat ]] || return 1
  cmd=$(<$root/$p/cmdline)
  cmd=${cmd//$'\0'/ }
  [[ ${cmd% } == '/bin/sh -c /usr/bin/zsh' ]] || return 1
  stat=$(<$root/$p/stat)
  f=(${=stat##*) })
  [[ -r $root/${f[2]}/comm ]] || return 1
  [[ $(<$root/${f[2]}/comm) == ghostty ]]
}

_ghostty_tabs_is_tab_shell || { unfunction _ghostty_tabs_is_tab_shell; return 0 }
unfunction _ghostty_tabs_is_tab_shell

zmodload -F zsh/files b:zf_mv b:zf_rm b:zf_mkdir
autoload -Uz add-zsh-hook

typeset -g _ghostty_tabs_dir=$HOME/.local/state/ghostty-tabs/live
typeset -g _ghostty_tabs_file=$_ghostty_tabs_dir/$$.json
typeset -g _ghostty_tabs_tmp=$_ghostty_tabs_dir/.$$.json.tmp
typeset -g _ghostty_tabs_running=
typeset -g _ghostty_tabs_last=
typeset -g _ghostty_tabs_start=
typeset -g _ghostty_tabs_first=1

_ghostty_tabs_read_start() {
  emulate -L zsh
  local stat
  local -a f
  [[ -r /proc/$$/stat ]] || return 1
  stat=$(</proc/$$/stat)
  f=(${=stat##*) })
  [[ ${f[20]} == <1-> ]] || return 1
  _ghostty_tabs_start=${f[20]}
}
_ghostty_tabs_read_start || { unfunction _ghostty_tabs_read_start; return 0 }
unfunction _ghostty_tabs_read_start

# Sets REPLY to the JSON string body of $1.
_ghostty_tabs_escape() {
  emulate -L zsh
  local s=$1 c i
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  if [[ $s == *[[:cntrl:]]* ]]; then
    for i in {1..31}; do
      printf -v c '\\u%04x' $i
      s=${s//${(#)i}/$c}
    done
  fi
  REPLY=$s
}

_ghostty_tabs_write() {
  emulate -L zsh
  (( ZSH_SUBSHELL )) && return 0
  local cwd run last REPLY
  [[ -d $_ghostty_tabs_dir ]] || {
    zf_mkdir -p -m 700 ${_ghostty_tabs_dir:h} $_ghostty_tabs_dir 2>/dev/null
  }
  _ghostty_tabs_escape $PWD; cwd=$REPLY
  _ghostty_tabs_escape $_ghostty_tabs_running; run=$REPLY
  _ghostty_tabs_escape $_ghostty_tabs_last; last=$REPLY
  {
    print -r -- "{\"pid\": $$, \"start\": $_ghostty_tabs_start, \"cwd\": \"$cwd\", \"running\": \"$run\", \"last\": \"$last\"}" > $_ghostty_tabs_tmp &&
      zf_mv -f $_ghostty_tabs_tmp $_ghostty_tabs_file
  } 2>/dev/null
  return 0
}

_ghostty_tabs_chpwd() {
  _ghostty_tabs_write
}

_ghostty_tabs_preexec() {
  _ghostty_tabs_running=$1
  _ghostty_tabs_last=$1
  _ghostty_tabs_write
}

_ghostty_tabs_precmd() {
  local rewrite=
  [[ -n $_ghostty_tabs_running ]] && rewrite=1
  _ghostty_tabs_running=
  if [[ -n $_ghostty_tabs_first ]]; then
    _ghostty_tabs_first=
    local run=$GHOSTTY_TABS_RUN prefill=$GHOSTTY_TABS_PREFILL note=$GHOSTTY_TABS_NOTE
    unset GHOSTTY_TABS_ID GHOSTTY_TABS_RUN GHOSTTY_TABS_PREFILL GHOSTTY_TABS_NOTE
    [[ -n $note ]] && print -r -- "$note"
    if [[ -n $prefill ]]; then
      _ghostty_tabs_last=$prefill
      print -rz -- "$prefill"
    fi
    if [[ -n $run ]]; then
      _ghostty_tabs_last=$run
      _ghostty_tabs_running=$run
      _ghostty_tabs_write
      eval "$run"
      _ghostty_tabs_running=
    fi
    rewrite=1
  fi
  [[ -n $rewrite ]] && _ghostty_tabs_write
  return 0
}

_ghostty_tabs_zshexit() {
  (( ZSH_SUBSHELL )) && return 0
  zf_rm -f $_ghostty_tabs_file $_ghostty_tabs_tmp 2>/dev/null
}

add-zsh-hook chpwd _ghostty_tabs_chpwd
add-zsh-hook preexec _ghostty_tabs_preexec
add-zsh-hook precmd _ghostty_tabs_precmd
add-zsh-hook zshexit _ghostty_tabs_zshexit
_ghostty_tabs_write
