export PATH=$HOME/.cargo/bin:/usr/local/bin:$PATH

export ZSH="$HOME/.oh-my-zsh"

zstyle ':omz:update' mode auto
zstyle ':omz:update' frequency 7

ZSH_THEME="hipony"
DISABLE_COMPFIX="true"

plugins=(git zsh-autosuggestions zsh-syntax-highlighting)

source $ZSH/oh-my-zsh.sh
export EDITOR='vim'
# User configuration
# ...
[ -f ~/.zshrc.local ] && source ~/.zshrc.local
