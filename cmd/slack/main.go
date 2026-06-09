package main

import (
	"os"

	"github.com/ryangerardwilson/slack/internal/app"
)

func main() {
	os.Exit(app.Main(os.Args[1:]))
}
