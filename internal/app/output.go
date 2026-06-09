package app

import (
	"archive/zip"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

type kv struct {
	Key   string
	Value string
}

func (rt *Runtime) printSections(rows [][]kv) {
	for index, row := range rows {
		if index > 0 {
			fmt.Fprintln(rt.Stdout)
		}
		for _, item := range row {
			fmt.Fprintf(rt.Stdout, "%s: %s\n", item.Key, item.Value)
		}
	}
}

func (rt *Runtime) printJSON(value any) error {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	_, err = fmt.Fprintln(rt.Stdout, string(data))
	return err
}

func redactToken(token string) string {
	token = strings.TrimSpace(token)
	if token == "" {
		return "-"
	}
	if len(token) <= 10 {
		return token[:min(len(token), 4)] + "..."
	}
	return token[:5] + "..." + token[len(token)-4:]
}

func listRegisteredContacts(rt *Runtime, contacts Contacts) {
	if len(contacts) == 0 {
		fmt.Fprintln(rt.Stdout, "No contacts registered.")
		return
	}
	keys := make([]string, 0, len(contacts))
	for key := range contacts {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	var rows [][]kv
	for _, key := range keys {
		rows = append(rows, []kv{{"label", key}, {"target", contacts[key]}})
	}
	rt.printSections(rows)
}

func zipDir(sourceDir, destination string) error {
	out, err := os.Create(destination)
	if err != nil {
		return err
	}
	defer out.Close()
	writer := zip.NewWriter(out)
	defer writer.Close()

	return filepath.WalkDir(sourceDir, func(path string, d os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if d.IsDir() {
			return nil
		}
		info, err := d.Info()
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(sourceDir, path)
		if err != nil {
			return err
		}
		header, err := zip.FileInfoHeader(info)
		if err != nil {
			return err
		}
		header.Name = filepath.ToSlash(rel)
		header.Method = zip.Deflate
		part, err := writer.CreateHeader(header)
		if err != nil {
			return err
		}
		in, err := os.Open(path)
		if err != nil {
			return err
		}
		defer in.Close()
		_, err = io.Copy(part, in)
		return err
	})
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
