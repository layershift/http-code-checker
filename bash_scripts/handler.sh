#!/bin/bash

# Script for Plesk domain and snapshot management API calls

# Configuration
BASE_URL="https://dontdeletezoltan.man-1.solus.stage.town/api/v1"
HOSTNAME=$(hostname)

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print usage
usage() {
    echo "Usage: $0 [OPTION] [ARGUMENT]"
    echo "Options:"
    echo "  --add-server                       Add server using hostname"
    echo "  --add-domain DOMAIN                Add specific domain"
    echo "  --add-all-domains                  Add all domains from Plesk"
    echo "  --make-snapshot DOMAIN             Create snapshot for specific domain"
    echo "  --make-baseline-snapshot DOMAIN    Create snapshot for specific domain and set as baseline"
    echo "  --make-all-snapshots               Create snapshots for all domains"
    echo "  --make-all-baseline-snapshots      Create snapshots for all domains and set as baseline"
    echo "  --report DOMAIN                    Generate report for specific domain"
    echo "  --report-all                       Generate server report"
    echo "  --help                             Show this help message"
}

# Function to check if curl is available
check_curl() {
    if ! command -v curl &> /dev/null; then
        echo -e "${RED}Error: curl is not installed${NC}"
        exit 1
    fi
}

# Function to check if plesk is available (for domain listing)
check_plesk() {
    if ! command -v plesk &> /dev/null; then
        echo -e "${RED}Error: plesk command not found. Are you running this on a Plesk server?${NC}"
        exit 1
    fi
}

# Main script logic
main() {
    check_curl
    
    if [ $# -eq 0 ]; then
        usage
        exit 1
    fi
    
    case "$1" in
        --add-server)
            echo -e "${BLUE}Adding server: $HOSTNAME${NC}"
            curl -X POST "${BASE_URL}/servers/" \
                -H "Content-Type: application/json" \
                -d "{\"name\": \"$HOSTNAME\", \"description\": \"$HOSTNAME\"}"
            echo ""
            ;;
            
                    
        --add-domain)
            if [ -z "$2" ]; then
                echo -e "${RED}Error: Domain name required${NC}"
                usage
                exit 1
            fi
            echo -e "${BLUE}Adding domain: $2${NC}"
            curl -X POST "${BASE_URL}/dispatch_comparison/" \
                -H "Content-Type: application/json" \
                -d "{\"domain\": \"$2\"}"
            echo ""
            ;;
            
        --add-all-domains)
            check_plesk
            echo -e "${BLUE}Adding all domains from Plesk...${NC}"
            plesk bin domain --list | while read domain; do
                if [ ! -z "$domain" ]; then
                    echo -e "${YELLOW}Adding domain: $domain${NC}"
                    curl -X POST "${BASE_URL}/sites/" \
                        -H "Content-Type: application/json" \
                        -d "{\"name\":\"$domain\"}"
                    echo ""
                fi
            done
            echo -e "${GREEN}All domains processed${NC}"
            ;;
            
        --make-snapshot)
            if [ -z "$2" ]; then
                echo -e "${RED}Error: Domain name required${NC}"
                usage
                exit 1
            fi
            echo -e "${BLUE}Creating snapshot for domain: $2${NC}"
            curl -X POST "${BASE_URL}/snapshots/" \
                -H "Content-Type: application/json" \
                -d "{\"name\":\"$2\"}"
            echo ""
            ;;

        --make-baseline-snapshot)
            if [ -z "$2" ]; then
                echo -e "${RED}Error: Domain name required${NC}"
                usage
                exit 1
            fi
            echo -e "${BLUE}Creating snapshot for domain: $2${NC}"
            curl -X POST "${BASE_URL}/snapshots/" \
                -H "Content-Type: application/json" \
                -d "{\"name\":\"$2\", \"set_as_baseline\": \"true\"}"
            echo ""
            ;;
            
        --make-all-snapshots)
            check_plesk
            echo -e "${BLUE}Creating snapshots for all domains...${NC}"
            plesk bin domain --list | while read domain; do
                if [ ! -z "$domain" ]; then
                    echo -e "${YELLOW}Creating snapshot for domain: $domain${NC}"
                    curl -X POST "${BASE_URL}/snapshots/" \
                        -H "Content-Type: application/json" \
                        -d "{\"name\":\"$domain\"}"
                    echo ""
                fi
            done
            echo -e "${GREEN}All snapshots created${NC}"
            ;;
        
        --make-all-baseline-snapshots)
            check_plesk
            echo -e "${BLUE}Creating snapshots for all domains...${NC}"
            plesk bin domain --list | while read domain; do
                if [ ! -z "$domain" ]; then
                    echo -e "${YELLOW}Creating snapshot for domain: $domain${NC}"
                    curl -X POST "${BASE_URL}/snapshots/" \
                        -H "Content-Type: application/json" \
                        -d "{\"name\":\"$domain\", \"set_as_baseline\": \"true\"}"
                    echo ""
                fi
            done
            echo -e "${GREEN}All snapshots created${NC}"
            ;;

        --report)
            if [ -z "$2" ]; then
                echo -e "${RED}Error: Domain name required${NC}"
                usage
                exit 1
            fi
            echo -e "${BLUE}Generating report for domain: $2${NC}"
            curl -X POST "${BASE_URL}/dispatch_comparison/" \
                -H "Content-Type: application/json" \
                -d "{\"domain\": \"$2\"}"
            echo ""
            ;;
            
        --report-all)
            echo -e "${BLUE}Generating server report for: $HOSTNAME${NC}"
            curl -X POST "${BASE_URL}/dispatch_comparison/" \
                -H "Content-Type: application/json" \
                -d "{\"server\": \"$HOSTNAME\"}"
            echo ""
            ;;
            
        --help)
            usage
            ;;
            
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            usage
            exit 1
            ;;
    esac
}

# Run the main function with all arguments
main "$@"