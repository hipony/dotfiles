username() {
  echo "%{${fg_bold[yellow]}%}%n%{$reset_color%}"
  # echo "%{${fg_bold[yellow]}%}%n@%m%{$reset_color%}"
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

PROMPT='$(username):$(directory) $(git_prompt_info)$(privilege) '

RPROMPT='$(error_code)$(current_time)'

ZSH_THEME_GIT_PROMPT_PREFIX="%{$fg[blue]%}("
ZSH_THEME_GIT_PROMPT_SUFFIX="%{$reset_color%} "
ZSH_THEME_GIT_PROMPT_DIRTY="%{$fg[blue]%})%{$fg[red]%}✗%{$reset_color%}"
ZSH_THEME_GIT_PROMPT_CLEAN="%{$fg[blue]%})"
