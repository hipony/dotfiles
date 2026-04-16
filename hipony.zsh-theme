username() {
  if [ -n "$SSH_CONNECTION" ]; then
    echo "%{${fg_bold[green]}%}%n@%m%{$reset_color%}"
  else
    echo "%{${fg_bold[yellow]}%}%n%{$reset_color%}"
  fi
}

directory() {
  echo "%{$fg[green]%}%2~%{$reset_color%}"
}

privilege() {
  echo "%{$fg[yellow]%}%#%{$reset_color%}"
}

current_time() {
  echo "%*"
}

error_code() {
  echo "%(?,,%{${fg_bold[white]}%}[%?]%{$reset_color%} )"
}

venv_info() {
  [ -n "$VIRTUAL_ENV" ] && echo "%{$fg[cyan]%}($(basename $VIRTUAL_ENV)) %{$reset_color%}"
}

preexec() { _cmd_start=$SECONDS }
precmd() {
  if [ -n "$_cmd_start" ]; then
    local duration=$(( SECONDS - _cmd_start ))
    if [ $duration -gt 5 ]; then
      _cmd_duration="%{$fg[magenta]%}${duration}s%{$reset_color%} "
    else
      _cmd_duration=""
    fi
    unset _cmd_start
  else
    _cmd_duration=""
  fi
}

PROMPT='$(venv_info)$(username):$(directory) $(git_prompt_info)$(privilege) '

RPROMPT='$_cmd_duration$(error_code)$(current_time)'

ZSH_THEME_GIT_PROMPT_PREFIX="%{$fg[blue]%}("
ZSH_THEME_GIT_PROMPT_SUFFIX="%{$reset_color%} "
ZSH_THEME_GIT_PROMPT_DIRTY="%{$fg[blue]%})%{$fg[red]%}✗%{$reset_color%}"
ZSH_THEME_GIT_PROMPT_CLEAN="%{$fg[blue]%})"
