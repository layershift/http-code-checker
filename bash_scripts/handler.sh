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
    echo "Usage: $0 [OPTION] [ARGUMENT] [--ticket TICKET_ID]"
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
    echo "  --delete-domain DOMAIN             Delete domain and all associated files"
    echo "  --delete-server                    Delete this server and all associated files"
    echo "  --delete-snapshot ID               Delete snapshot by ID and its associated file"
    echo "  --check-server                     Check baseline health for this server"
    echo "  --check-ticket TICKET_ID           Check monitoring status by ticket ID (full output)"
    echo "  --check-ticket-short TICKET_ID     Check monitoring status (returns only true/false)"
    echo "  --check-ticket-status TICKET_ID    Check ticket status with colored output and exit code"
    echo "  --help                             Show this help message"
    echo ""
    echo "Optional ticket parameter:"
    echo "  --ticket TICKET_ID                 Add ticket ID to dispatch comparison requests (default: None)"
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

# Function to get domain IP from Plesk
get_domain_ip() {
    local domain="$1"
    local ip=$(plesk db -N -e "SELECT ip.ip_address FROM domains d INNER JOIN DomainServices ds ON ds.dom_id = d.id INNER JOIN IpAddressesCollections ic ON ic.ipCollectionId = ds.ipCollectionId INNER JOIN IP_Addresses ip ON ip.id = ic.ipAddressId WHERE d.name = '$domain' AND ds.type = 'web' AND ds.status = 0 AND d.status = 0 AND ip.ip_address NOT LIKE '%:%' LIMIT 1;" 2>/dev/null)

    if [ -z "$ip" ]; then
        echo "$(hostname -I | awk '{print $1}')"
    else
        echo "$ip"
    fi
}

# Function to extract ticket_id from arguments
get_ticket_id() {
    local ticket="None"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --ticket)
                ticket="$2"
                shift 2
                ;;
            *)
                shift
                ;;
        esac
    done
    echo "$ticket"
}

# Function to build JSON payload with ticket_id
build_json_payload() {
    local key="$1"
    local value="$2"
    local ticket="$3"

    if [ "$ticket" = "None" ]; then
        echo "{\"$key\": \"$value\"}"
    else
        echo "{\"$key\": \"$value\", \"ticket_id\": \"$ticket\"}"
    fi
}

# Function to print monitoring status in a readable format
print_monitoring_status() {
    local response="$1"

    # Parse JSON with jq if available, otherwise use grep/sed
    if command -v jq &> /dev/null; then
        local status=$(echo "$response" | jq -r '.status // "unknown"')
        local job_status=$(echo "$response" | jq -r '.job_status // "unknown"')
        local is_complete=$(echo "$response" | jq -r '.is_complete // false')
        local total_sites=$(echo "$response" | jq -r '.progress.total_sites // 0')
        local processed=$(echo "$response" | jq -r '.progress.processed_sites // 0')
        local percentage=$(echo "$response" | jq -r '.progress.percentage // 0')
        local successful=$(echo "$response" | jq -r '.results.successful_sites // 0')
        local failed=$(echo "$response" | jq -r '.results.failed_sites // 0')
        local warnings=$(echo "$response" | jq -r '.results.warning_sites // 0')
        local is_healthy=$(echo "$response" | jq -r '.results.is_healthy // false')

        echo -e "\n${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${BLUE}📊 MONITORING STATUS REPORT${NC}"
        echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

        # Status badge
        case "$job_status" in
            pending)
                echo -e "Status:     ${YELLOW}⏳ PENDING${NC}"
                ;;
            processing)
                echo -e "Status:     ${BLUE}🔄 PROCESSING${NC}"
                ;;
            completed)
                echo -e "Status:     ${GREEN}✅ COMPLETED${NC}"
                ;;
            failed)
                echo -e "Status:     ${RED}❌ FAILED${NC}"
                ;;
            partial)
                echo -e "Status:     ${YELLOW}⚠️ PARTIAL${NC}"
                ;;
            *)
                echo -e "Status:     $job_status"
                ;;
        esac

        # Progress bar
        if [ "$total_sites" -gt 0 ]; then
            local bar_length=30
            local filled=$((percentage * bar_length / 100))
            local empty=$((bar_length - filled))

            echo -e "\n${BLUE}Progress:   ${NC}[$(printf '%0.s█' $(seq 1 $filled))$(printf '%0.s░' $(seq 1 $empty))] ${percentage}%"
            echo -e "            ${processed}/${total_sites} sites processed"
        fi

        # Results if complete
        if [ "$is_complete" = "true" ]; then
            echo -e "\n${BLUE}Results:${NC}"
            echo -e "  ✅ Successful: ${GREEN}${successful}${NC}"
            echo -e "  ❌ Failed:     ${RED}${failed}${NC}"
            echo -e "  ⚠️ Warnings:   ${YELLOW}${warnings}${NC}"

            if [ "$is_healthy" = "true" ]; then
                echo -e "\n${GREEN}✅ All systems healthy!${NC}"
            else
                echo -e "\n${RED}⚠️ Issues detected! Check details above.${NC}"
            fi
        else
            echo -e "\n${YELLOW}⏳ Job still in progress...${NC}"
        fi

        echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
    else
        # Fallback if jq is not installed
        echo "$response" | python3 -m json.tool 2>/dev/null || echo "$response"
    fi
}

# Function to check if a ticket is completed (returns true/false)
check_ticket_completed() {
    local ticket_id="$1"

    # Query the status endpoint
    local response=$(curl -s -X GET "${BASE_URL}/monitoring/status/?message_id=${ticket_id}" 2>/dev/null)

    # Check if the job is complete
    if echo "$response" | grep -q '"is_complete":true'; then
        echo "true"
        return 0
    else
        echo "false"
        return 1
    fi
}

# Function to check ticket status and exit with appropriate code
check_ticket_status() {
    local ticket_id="$1"
    local response=$(curl -s -X GET "${BASE_URL}/monitoring/status/?message_id=${ticket_id}" 2>/dev/null)

    if echo "$response" | grep -q '"is_complete":true'; then
        echo -e "${GREEN}✅ Ticket $ticket_id is COMPLETED${NC}"
        return 0
    else
        echo -e "${RED}❌ Ticket $ticket_id is NOT COMPLETED${NC}"
        return 1
    fi
}

# Main script logic
main() {
    check_curl

    if [ $# -eq 0 ]; then
        usage
        exit 1
    fi

    # Store all arguments for processing
    ALL_ARGS=("$@")

    # Get ticket_id from arguments
    TICKET_ID=$(get_ticket_id "${ALL_ARGS[@]}")

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
            check_plesk
            DOMAIN="$2"
            IP=$(get_domain_ip "$DOMAIN")
            echo -e "${BLUE}Adding domain: $DOMAIN with IP: $IP${NC}"
            curl -X POST "${BASE_URL}/sites/" \
                -H "Content-Type: application/json" \
                -d "{\"name\":\"$DOMAIN\", \"ip\":\"$IP\"}"
            echo ""
            ;;

        --add-all-domains)
            check_plesk
            echo -e "${BLUE}Adding all domains from Plesk...${NC}"
            plesk bin domain --list | while read domain; do
                if [ ! -z "$domain" ]; then
                    IP=$(get_domain_ip "$domain")
                    echo -e "${YELLOW}Adding domain: $domain with IP: $IP${NC}"
                    curl -X POST "${BASE_URL}/sites/" \
                        -H "Content-Type: application/json" \
                        -d "{\"name\":\"$domain\", \"ip\":\"$IP\"}"
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
            PAYLOAD=$(build_json_payload "domain" "$2" "$TICKET_ID")
            curl -X POST "${BASE_URL}/dispatch_comparison/" \
                -H "Content-Type: application/json" \
                -d "$PAYLOAD"
            echo ""
            ;;

        --report-all)
            echo -e "${BLUE}Generating server report for: $HOSTNAME${NC}"
            PAYLOAD=$(build_json_payload "server" "$HOSTNAME" "$TICKET_ID")
            curl -X POST "${BASE_URL}/dispatch_comparison/" \
                -H "Content-Type: application/json" \
                -d "$PAYLOAD"
            echo ""
            ;;

        --delete-domain)
            if [ -z "$2" ]; then
                echo -e "${RED}Error: Domain name required${NC}"
                usage
                exit 1
            fi
            echo -e "${RED}⚠️  WARNING: This will delete domain $2 and ALL associated files!${NC}"
            echo -e "${YELLOW}Are you sure? Type 'yes' to confirm: ${NC}"
            read -r confirmation
            if [ "$confirmation" = "yes" ]; then
                echo -e "${BLUE}Deleting domain: $2 and all associated files...${NC}"
                curl -X DELETE "${BASE_URL}/sites/$2/delete/"
                echo ""
            else
                echo -e "${RED}Deletion cancelled${NC}"
            fi
            ;;

        --delete-server)
            echo -e "${RED}⚠️  WARNING: This will delete server $HOSTNAME and ALL associated domains, snapshots, comparisons, and files!${NC}"
            echo -e "${YELLOW}Are you sure? Type 'yes' to confirm: ${NC}"
            read -r confirmation
            if [ "$confirmation" = "yes" ]; then
                echo -e "${BLUE}Deleting server: $HOSTNAME and all associated files...${NC}"
                ENCODED_SERVER=$(echo "$HOSTNAME" | sed 's/ /%20/g')
                curl -X DELETE "${BASE_URL}/servers/$ENCODED_SERVER/delete/"
                echo ""
            else
                echo -e "${RED}Deletion cancelled${NC}"
            fi
            ;;

        --delete-snapshot)
            if [ -z "$2" ]; then
                echo -e "${RED}Error: Snapshot ID required${NC}"
                usage
                exit 1
            fi
            echo -e "${RED}⚠️  WARNING: This will delete snapshot $2 and its associated file!${NC}"
            echo -e "${YELLOW}Are you sure? Type 'yes' to confirm: ${NC}"
            read -r confirmation
            if [ "$confirmation" = "yes" ]; then
                echo -e "${BLUE}Deleting snapshot: $2 and its associated file...${NC}"
                curl -X DELETE "${BASE_URL}/snapshots/$2/delete/"
                echo ""
            else
                echo -e "${RED}Deletion cancelled${NC}"
            fi
            ;;

        --check-server)
            echo -e "${BLUE}Checking baseline health for server: $HOSTNAME${NC}"
            curl -X POST "${BASE_URL}/check-server-baseline/" \
                -H "Content-Type: application/json" \
                -d "{\"server\": \"$HOSTNAME\"}"
            echo ""
            ;;

        --check-ticket)
            if [ -z "$2" ]; then
                echo -e "${RED}Error: Ticket ID required${NC}"
                usage
                exit 1
            fi
            TICKET_TO_CHECK="$2"
            echo -e "${BLUE}Checking monitoring status for ticket: $TICKET_TO_CHECK${NC}"

            RESPONSE=$(curl -s -X GET "${BASE_URL}/monitoring/status/?message_id=${TICKET_TO_CHECK}")

            if echo "$RESPONSE" | grep -q '"status":"success"'; then
                print_monitoring_status "$RESPONSE"
            else
                echo -e "${RED}❌ No monitoring job found for ticket: $TICKET_TO_CHECK${NC}"
                echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
            fi
            ;;

        --check-ticket-short)
            if [ -z "$2" ]; then
                echo -e "${RED}Error: Ticket ID required${NC}"
                usage
                exit 1
            fi
            TICKET_TO_CHECK="$2"
            check_ticket_completed "$TICKET_TO_CHECK"
            ;;

        --check-ticket-status)
            if [ -z "$2" ]; then
                echo -e "${RED}Error: Ticket ID required${NC}"
                usage
                exit 1
            fi
            TICKET_TO_CHECK="$2"
            check_ticket_status "$TICKET_TO_CHECK"
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