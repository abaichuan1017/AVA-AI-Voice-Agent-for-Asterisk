package main

import (
	_ "embed"
	"fmt"
	"os"
	"os/exec"
)

//go:embed integration_backup.py
var integrationBackupScript string

func backupIntegrationState(repoRoot, backupRoot string) error {
	if err := os.MkdirAll(backupRoot, 0700); err != nil {
		return err
	}
	destination, err := os.MkdirTemp(backupRoot, "integration-state.")
	if err != nil {
		return err
	}
	cmd := exec.Command("python3", "-c", integrationBackupScript, repoRoot, destination)
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("integration snapshot failed; retain Calendly state and managed secrets with a private manual backup before updating: %w", err)
	}
	printUpdateInfo("Private integration snapshot: %s", destination)
	return nil
}
