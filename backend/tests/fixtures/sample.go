package main

import (
	"fmt"
	"net/http"
)

type Config struct {
	Name  string
	Value int
}

type Handler interface {
	Handle(w http.ResponseWriter, r *http.Request)
}

func NewConfig(name string) *Config {
	return &Config{Name: name}
}

func (c *Config) GetName() string {
	return c.Name
}

func main() {
	cfg := NewConfig("test")
	fmt.Println(cfg.GetName())
}
