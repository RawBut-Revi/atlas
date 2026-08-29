package main

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha1"
	"crypto/sha256"
	"encoding/base32"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strings"
	"time"

	"golang.org/x/crypto/bcrypt"
	"golang.org/x/crypto/pbkdf2"
)

type Vault struct {
	Username        string `json:"username"`
	PasswordHash    string `json:"password_hash"`
	TOTPSecret      string `json:"totp_secret"`
	EncryptedAESKey string `json:"encrypted_aes_key"`
	AESKeySalt      string `json:"aes_key_salt"`
}

type AuthService struct{}

func NewAuthService() *AuthService {
	return &AuthService{}
}

func (a *AuthService) getExecutableDir() string {
	ex, err := os.Executable()
	if err != nil {
		return "."
	}
	return filepath.Dir(ex)
}

func (a *AuthService) GetVaultPath() string {
	return filepath.Join(a.getExecutableDir(), "atlas_vault.json")
}

func (a *AuthService) IsSetupComplete() bool {
	path := a.GetVaultPath()
	info, err := os.Stat(path)
	if err != nil {
		return false
	}
	if info.Size() == 0 {
		return false
	}

	data, err := os.ReadFile(path)
	if err != nil {
		return false
	}

	var vault Vault
	if err := json.Unmarshal(data, &vault); err != nil {
		return false
	}

	return vault.Username != "" && vault.PasswordHash != "" && vault.TOTPSecret != ""
}

func (a *AuthService) SetupAccount(username, password, totpSecret string) error {
	if a.IsSetupComplete() {
		return errors.New("account already setup")
	}

	hash, err := bcrypt.GenerateFromPassword([]byte(password), bcrypt.DefaultCost)
	if err != nil {
		return err
	}

	aesKey := make([]byte, 32)
	if _, err := rand.Read(aesKey); err != nil {
		return err
	}

	salt := make([]byte, 16)
	if _, err := rand.Read(salt); err != nil {
		return err
	}

	derivedKey := pbkdf2.Key([]byte(password), salt, 100000, 32, sha256.New)

	encryptedAESKey := make([]byte, 32)
	for i := range aesKey {
		encryptedAESKey[i] = aesKey[i] ^ derivedKey[i]
	}

	vault := Vault{
		Username:        username,
		PasswordHash:    string(hash),
		TOTPSecret:      totpSecret,
		EncryptedAESKey: hex.EncodeToString(encryptedAESKey),
		AESKeySalt:      hex.EncodeToString(salt),
	}

	data, err := json.MarshalIndent(vault, "", "  ")
	if err != nil {
		return err
	}

	return os.WriteFile(a.GetVaultPath(), data, 0600)
}

func (a *AuthService) GenerateTOTPSecret() (string, error) {
	secretBytes := make([]byte, 20)
	if _, err := rand.Read(secretBytes); err != nil {
		return "", err
	}

	secret := base32.StdEncoding.WithPadding(base32.NoPadding).EncodeToString(secretBytes)

	return secret, nil
}

func (a *AuthService) ValidateTOTP(secret, code string) bool {
	secret = strings.ToUpper(secret)
	key, err := base32.StdEncoding.WithPadding(base32.NoPadding).DecodeString(secret)
	if err != nil {
		key, err = base32.StdEncoding.DecodeString(secret)
		if err != nil {
			return false
		}
	}

	now := time.Now().Unix()
	timeStep := int64(30)
	currentStep := now / timeStep

	steps := []int64{currentStep, currentStep - 1, currentStep + 1}

	for _, step := range steps {
		if a.generateTOTPCode(key, step) == code {
			return true
		}
	}

	return false
}

func (a *AuthService) generateTOTPCode(key []byte, step int64) string {
	msg := make([]byte, 8)
	binary.BigEndian.PutUint64(msg, uint64(step))

	h := hmac.New(sha1.New, key)
	h.Write(msg)
	hash := h.Sum(nil)

	offset := hash[len(hash)-1] & 0x0f

	binaryCode := binary.BigEndian.Uint32(hash[offset:offset+4]) & 0x7fffffff

	code := int(binaryCode) % int(math.Pow10(6))

	return fmt.Sprintf("%06d", code)
}

func (a *AuthService) Login(username, password, totpCode string) (bool, error) {
	path := a.GetVaultPath()
	data, err := os.ReadFile(path)
	if err != nil {
		return false, err
	}

	var vault Vault
	if err := json.Unmarshal(data, &vault); err != nil {
		return false, err
	}

	if vault.Username != username {
		return false, nil
	}

	if err := bcrypt.CompareHashAndPassword([]byte(vault.PasswordHash), []byte(password)); err != nil {
		return false, nil
	}

	if !a.ValidateTOTP(vault.TOTPSecret, totpCode) {
		return false, nil
	}

	return true, nil
}
