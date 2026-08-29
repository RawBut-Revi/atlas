package config

import (
	"log"
	"strings"

	"github.com/joho/godotenv"
	"github.com/spf13/viper"
)

type Config struct {
	Environment string
	Broker      BrokerConfig
	Database    DatabaseConfig
}

type BrokerConfig struct {
	Provider    string
	APIKey      string
	Secret      string
	RedirectURL string
}

type DatabaseConfig struct {
	Path string
}

func LoadConfig() (*Config, error) {
	// Attempt to load .env file if it exists, but don't fail if it doesn't
	// (useful for production environments where env vars are set directly)
	_ = godotenv.Load()

	viper.AutomaticEnv()
	viper.SetEnvKeyReplacer(strings.NewReplacer(".", "_"))

	// Defaults
	viper.SetDefault("ENVIRONMENT", "development")
	viper.SetDefault("DATABASE.PATH", "../research/data/market_data.duckdb")

	var cfg Config
	
	cfg.Environment = viper.GetString("ENVIRONMENT")
	cfg.Broker.Provider = viper.GetString("BROKER_PROVIDER")
	cfg.Broker.APIKey = viper.GetString("BROKER_API_KEY")
	cfg.Broker.Secret = viper.GetString("BROKER_SECRET")
	cfg.Broker.RedirectURL = viper.GetString("BROKER_REDIRECT_URL")
	cfg.Database.Path = viper.GetString("DATABASE_PATH")

	// If using nested struct unmarshaling, we could use viper.Unmarshal(&cfg)
	// but direct assignment is often clearer for small configs.

	log.Printf("Configuration loaded for environment: %s", cfg.Environment)
	return &cfg, nil
}
