import streamlit as st
import requests
import pandas as pd
import io
import traceback
import unicodedata
import re
import difflib
from espn_api.baseball import League

st.set_page_config(page_title="MLB Roster Exporter", page_icon="⚾", layout="wide")
st.title("⚾ Fantasy Baseball Scraper & Merger")

# --- INITIALIZE SESSION STATE ---
if 'step' not in st.session_state:
    st.session_state.step = 1
if 'consensus_df' not in st.session_state:
    st.session_state.consensus_df = None
if 'raw_preview_df' not in st.session_state:
    st.session_state.raw_preview_df = None
if 'raw_preview_title' not in st.session_state:
    st.session_state.raw_preview_title = ""
if 'raw_excel_data' not in st.session_state:
    st.session_state.raw_excel_data = None
if 'espn_rosters' not in st.session_state:
    st.session_state.espn_rosters = []
if 'espn_fa' not in st.session_state:
    st.session_state.espn_fa = []
if 'master_list' not in st.session_state:
    st.session_state.master_list = []
if 'matches' not in st.session_state:
    st.session_state.matches = {}
if 'unmatched' not in st.session_state:
    st.session_state.unmatched = []

# --- HELPER FUNCTIONS ---
def auto_adjust_column_width(writer, df, sheet_name):
    worksheet = writer.sheets[sheet_name]
    for col_idx, column in enumerate(df.columns):
        column_width = max(df[column].astype(str).map(len).max(), len(column))
        worksheet.column_dimensions[chr(65 + col_idx)].width = column_width + 2

def normalize_name(name):
    """Removes accents, punctuation, and suffixes to improve matching."""
    if not isinstance(name, str): return ""
    name = unicodedata.normalize('NFKD', name).encode('ASCII', 'ignore').decode('utf-8').lower()
    name = re.sub(r'[^a-z\s]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    name = name.replace(' jr', '').replace(' sr', '').replace(' ii', '').replace(' iii', '')
    return name

# ==========================================
# CONFIGURATION SETTINGS
# ==========================================
st.header("⚙️ Configuration")

# --- FanGraphs Settings ---
st.subheader("1. FanGraphs Settings")
col1, col2, col3 = st.columns(3)
with col1:
    player_type = st.radio("Player Type:", ["Batters", "Pitchers"], horizontal=True)
    is_pitcher = player_type == "Pitchers"
with col2:
    num_players = st.number_input("Players to Return:", min_value=10, max_value=1000, value=400, step=10)
with col3:
    min_systems = st.number_input("Min Systems for Consensus:", min_value=1, max_value=8, value=2)

st.markdown("**Projection Systems**")
pc1, pc2, pc3, pc4 = st.columns(4)
with pc1:
    use_steamer = st.checkbox("Steamer", value=True)
    use_fangraphsdc = st.checkbox("FanGraphs DC", value=True)
with pc2:
    use_thebat = st.checkbox("THE BAT", value=True)
    use_thebatx = st.checkbox("THE BAT X", value=True)
with pc3:
    use_atc = st.checkbox("ATC", value=True)
    use_oopsy = st.checkbox("OOPSY", value=True)
with pc4:
    if is_pitcher:
        use_zips = st.checkbox("ZiPS", value=False, disabled=True)
        use_zipsdc = st.checkbox("ZiPS DC", value=False, disabled=True)
    else:
        use_zips = st.checkbox("ZiPS", value=True)
        use_zipsdc = st.checkbox("ZiPS DC", value=True)

if is_pitcher:
    st.caption("⚠️ *ZiPS and ZiPS DC do not project Quality Starts (QS) and are disabled for Pitchers.*")

# --- ESPN Settings ---
st.subheader("2. ESPN Settings")
has_secrets = "SWID" in st.secrets and "ESPN_S2" in st.secrets

ecol_top, _ = st.columns([1, 2])
with ecol_top:
    use_defaults = st.checkbox("Use System Credentials (Secrets)", value=has_secrets)

ecol1, ecol2, ecol3, ecol4 = st.columns(4)
with ecol1:
    year = st.number_input("Year", value=2026)

if use_defaults and has_secrets:
    with ecol2:
        st.info("🔒 Using hidden system keys.")
    league_id = int(st.secrets.get("LEAGUE_ID", 11440))
    swid = st.secrets.get("SWID")
    espn_s2 = st.secrets.get("ESPN_S2")
else:
    with ecol2:
        league_id = st.number_input("League ID", value=11440)
    with ecol3:
        swid = st.text_input("SWID", type="password")
    with ecol4:
        espn_s2 = st.text_input("ESPN_S2", type="password")

st.divider()

# --- Logic Maps ---
proj_map = {
    'steamer': use_steamer, 'fangraphsdc': use_fangraphsdc, 
    'thebat': use_thebat, 'thebatx': use_thebatx, 
    'atc': use_atc, 'oopsy': use_oopsy, 
    'zips': use_zips, 'zipsdc': use_zipsdc
}
active_projections = [proj for proj, is_active in proj_map.items() if is_active]

if is_pitcher:
    stat_type = "pit"
    stats_to_keep = ['W', 'QS', 'SO', 'ERA', 'WHIP']
    stats_to_zscore = ['W', 'QS', 'SO', 'ERA', 'WHIP', 'SVHLD']
    final_cols = ['PlayerName', 'Team', 'playerid', 'W', 'QS', 'SO', 'ERA', 'WHIP', 'SVHLD']
else:
    stat_type = "bat"
    stats_to_keep = ['R', 'HR', 'RBI', 'SB', 'OBP', 'SLG']
    stats_to_zscore = ['R', 'HR', 'RBI', 'SB', 'OBP', 'SLG']
    final_cols = ['PlayerName', 'Team', 'playerid', 'R', 'HR', 'RBI', 'SB', 'OBP', 'SLG']


# ==========================================
# STEP 1: SCRAPE FANGRAPHS
# ==========================================
st.header("Step 1: Get Projections")

if st.button("Scrape FanGraphs", type="primary" if st.session_state.step == 1 else "secondary"):
    if not active_projections:
        st.error("Select at least one projection system.")
    else:
        with st.spinner(f"Fetching {player_type} projections..."):
            dfs_to_save = {}
            all_raw_data = []
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
                "Referer": "https://www.fangraphs.com/projections"
            }
            
            for proj in active_projections:
                url = "https://www.fangraphs.com/api/projections"
                params = {"type": proj, "stats": stat_type, "pos": "all", "team": "0", "players": "0", "lg": "all", "statgroup": "fantasy", "fantasypreset": "classic"}
                
                try:
                    response = requests.get(url, params=params, headers=headers, timeout=15)
                    response.raise_for_status()
                    data = response.json()
                    
                    player_data = data.get('data', []) if isinstance(data, dict) else data
                    if not player_data: continue
                        
                    df = pd.DataFrame(player_data)
                    
                    col_map = {str(c).lower(): c for c in df.columns}
                    rename_dict = {}
                    
                    for name_var in ['playername', 'name', 'fullname', 'player_name', 'player']:
                        if name_var in col_map: rename_dict[col_map[name_var]] = 'PlayerName'; break
                    for team_var in ['team', 'shortname', 'org']:
                        if team_var in col_map: rename_dict[col_map[team_var]] = 'Team'; break
                    for id_var in ['playerid', 'id']:
                        if id_var in col_map: rename_dict[col_map[id_var]] = 'playerid'; break
                    
                    for stat in stats_to_keep + (['SV', 'HLD'] if is_pitcher else []):
                        if stat.lower() in col_map: rename_dict[col_map[stat.lower()]] = stat
                            
                    df.rename(columns=rename_dict, inplace=True)
                    
                    if 'PlayerName' not in df.columns: continue
                    if 'Team' not in df.columns: df['Team'] = "FA"
                    
                    df = df.head(int(num_players))
                    
                    for col in stats_to_keep:
                        if col not in df.columns: df[col] = 0.0
                            
                    if is_pitcher:
                        for col in ['SV', 'HLD']:
                            if col not in df.columns: df[col] = 0.0
                        df['SVHLD'] = df['SV'] + df['HLD']
                    
                    existing_cols = [c for c in final_cols if c in df.columns]
                    df = df[existing_cols].copy()
                    
                    pr_columns = []
                    for stat in stats_to_zscore:
                        z_col = f"PR_{stat}"
                        pr_columns.append(z_col)
                        std_dev = df[stat].std()
                        if pd.isna(std_dev) or std_dev == 0:
                            df[z_col] = 0.0
                        else:
                            if is_pitcher and stat in ['ERA', 'WHIP']:
                                df[z_col] = (df[stat].mean() - df[stat]) / std_dev
                            else:
                                df[z_col] = (df[stat] - df[stat].mean()) / std_dev
                                
                    df['Total_PR'] = df[pr_columns].sum(axis=1)
                    
                    raw_df = df.copy()
                    raw_df['System'] = proj.upper()
                    
                    dfs_to_save[proj] = df
                    all_raw_data.append(raw_df)
                    
                except Exception as e:
                    st.error(f"Error fetching {proj}: {e}")

            if all_raw_data:
                combined_df = pd.concat(all_raw_data)
                agg_rules = {stat: 'mean' for stat in stats_to_zscore}
                agg_rules['Team'] = 'first'
                agg_rules['System'] = lambda x: ', '.join(x)
                
                consensus_df = combined_df.groupby(['playerid', 'PlayerName'], as_index=False).agg(agg_rules)
                consensus_df.rename(columns={'System': 'Sources'}, inplace=True)
                
                consensus_df['System_Count'] = consensus_df['Sources'].apply(lambda x: len(x.split(', ')))
                consensus_df = consensus_df[consensus_df['System_Count'] >= min_systems]
                consensus_df.drop(columns=['System_Count'], inplace=True)
                
                for stat in stats_to_zscore:
                    z_col = f"PR_{stat}"
                    std_dev = consensus_df[stat].std()
                    if pd.isna(std_dev) or std_dev == 0:
                        consensus_df[z_col] = 0.0
                    else:
                        if is_pitcher and stat in ['ERA', 'WHIP']:
                            consensus_df[z_col] = (consensus_df[stat].mean() - consensus_df[stat]) / std_dev
                        else:
                            consensus_df[z_col] = (consensus_df[stat] - consensus_df[stat].mean()) / std_dev

                consensus_df['Total_PR'] = consensus_df[pr_columns].sum(axis=1)
                consensus_df = consensus_df.sort_values(by='Total_PR', ascending=False)
                
                st.session_state.consensus_df = consensus_df
                dfs_to_save['Consensus'] = consensus_df
                
                # Setup Raw Preview & Excel Export
                if not consensus_df.empty:
                    st.session_state.raw_preview_df = consensus_df.head(10)
                    st.session_state.raw_preview_title = "Top 10 Consensus Preview"
                else:
                    st.session_state.raw_preview_df = dfs_to_save[active_projections[0]].head(10)
                    st.session_state.raw_preview_title = f"Top 10 {active_projections[0].upper()} Preview"

                excel_buffer = io.BytesIO()
                with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
                    if 'Consensus' in dfs_to_save and not dfs_to_save['Consensus'].empty:
                        dfs_to_save['Consensus'].to_excel(writer, sheet_name='Consensus', index=False)
                    for proj, df in dfs_to_save.items():
                        if proj != 'Consensus' and not df.empty:
                            df.to_excel(writer, sheet_name=proj, index=False)
                
                st.session_state.raw_excel_data = excel_buffer.getvalue()
                st.session_state.step = 2
                st.success("✅ FanGraphs Projections Loaded! You can download the raw data below, or proceed to Step 2 to sync with ESPN.")
            else:
                st.error("Failed to scrape FanGraphs data.")

# --- Show Step 1 Results & Download ---
if st.session_state.raw_preview_df is not None:
    st.subheader(f"{st.session_state.raw_preview_title}")
    st.dataframe(st.session_state.raw_preview_df, use_container_width=True, hide_index=True)
    
if st.session_state.raw_excel_data is not None:
    st.download_button(
        label="📥 Download Raw FanGraphs Excel",
        data=st.session_state.raw_excel_data,
        file_name=f"Raw_FanGraphs_{player_type.lower()}_projections.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# ==========================================
# STEP 2: FETCH ESPN & AUTO-MATCH
# ==========================================
st.divider()
st.header("Step 2: Sync ESPN & Match Players")

if st.session_state.consensus_df is not None:
    if st.button("Pull ESPN Rosters & Auto-Match", type="primary" if st.session_state.step == 2 else "secondary"):
        try:
            with st.spinner("Connecting to ESPN..."):
                league = League(league_id=league_id, year=year, espn_s2=espn_s2, swid=swid)
                st.success(f"Connected to: **{league.settings.name}**")
                
                excluded_slots = {'UTIL', 'BE', 'IL', 'IF', 'LF', 'CF', 'RF', 'SP', 'RP'}
                espn_rosters = []
                master_list = []
                
                # Fetch Rosters
                for team in league.teams:
                    for player in team.roster:
                        clean_slots = [s for s in player.eligibleSlots if s not in excluded_slots]
                        p_info = {
                            "ESPN_Name": player.name, "Fantasy Team": team.team_name,
                            "Pro Team": player.proTeam, "Injury Status": player.injuryStatus,
                            "Eligible Positions": ", ".join(clean_slots)
                        }
                        espn_rosters.append(p_info)
                        master_list.append(p_info)
                        
                # Fetch FA
                free_agents = league.free_agents(size=500)
                espn_fa = []
                for player in free_agents:
                    clean_slots = [s for s in player.eligibleSlots if s not in excluded_slots]
                    p_info = {
                        "ESPN_Name": player.name, "Fantasy Team": "Free Agent",
                        "Pro Team": player.proTeam, "Injury Status": player.injuryStatus,
                        "Eligible Positions": ", ".join(clean_slots)
                    }
                    espn_fa.append(p_info)
                    master_list.append(p_info)
                
                st.session_state.espn_rosters = espn_rosters
                st.session_state.espn_fa = espn_fa
                st.session_state.master_list = master_list

            with st.spinner("Executing Intelligent Matching..."):
                fg_df = st.session_state.consensus_df
                fg_records = fg_df.to_dict('records')
                for p in fg_records: p['norm_name'] = normalize_name(p['PlayerName'])
                
                fg_names_list = [p['norm_name'] for p in fg_records]
                fg_names_map = {p['norm_name']: p for p in fg_records}
                
                matches = {}
                unmatched = []
                
                for ep in master_list:
                    ep_name = ep['ESPN_Name']
                    ep_norm = normalize_name(ep_name)
                    ep_team = ep['Pro Team']
                    
                    # 1. Julio Rodriguez Exception
                    if "julio rodriguez" in ep_norm and ep_team == "SEA":
                        julios = [p for p in fg_records if "julio rodriguez" in p['norm_name']]
                        if julios:
                            if 'C' in ep['Eligible Positions']:
                                match = min(julios, key=lambda x: x['Total_PR']) # Catcher Julio
                            else:
                                match = max(julios, key=lambda x: x['Total_PR']) # OF Julio
                            matches[ep_name] = match['playerid']
                            continue

                    # 2. Pass 1: Direct Exact Match (Case-Sensitive)
                    exact_raw = [p for p in fg_records if p['PlayerName'] == ep_name]
                    if len(exact_raw) == 1:
                        matches[ep_name] = exact_raw[0]['playerid']
                        continue
                        
                    # 3. Pass 2: Exact Normalized Match
                    exact_norm = [p for p in fg_records if p['norm_name'] == ep_norm]
                    if len(exact_norm) == 1:
                        matches[ep_name] = exact_norm[0]['playerid']
                        continue
                        
                    # 4. Pass 3: Strict Fuzzy Match (Cutoff elevated to 85% to prevent bad guesses)
                    closest = difflib.get_close_matches(ep_norm, fg_names_list, n=1, cutoff=0.85)
                    if closest:
                        best_fg = fg_names_map[closest[0]]
                        matches[ep_name] = best_fg['playerid']
                    else:
                        # 5. Fallback: Manual Review Queue
                        guesses = difflib.get_close_matches(ep_norm, fg_names_list, n=5, cutoff=0.5)
                        guess_real_names = [fg_names_map[g]['PlayerName'] for g in guesses]
                        unmatched.append({
                            "espn_name": ep_name,
                            "team": ep_team,
                            "guesses": guess_real_names + ["Skip/Leave Blank"]
                        })

                st.session_state.matches = matches
                st.session_state.unmatched = unmatched
                st.session_state.step = 3
                st.success("✅ Matching Complete! Scroll down to review and export.")

        except Exception as e:
            st.error("Error connecting to ESPN.")
            st.code(traceback.format_exc())

# ==========================================
# STEP 3: MANUAL REVIEW & EXPORT
# ==========================================
if st.session_state.step >= 3:
    st.divider()
    st.header("Step 3: Resolve Unmatched & Export")
    
    fg_df = st.session_state.consensus_df
    
    if st.session_state.unmatched:
        st.warning(f"⚠️ {len(st.session_state.unmatched)} players could not be auto-matched perfectly. Please confirm closest guesses below:")
        
        with st.form("manual_match_form"):
            manual_selections = {}
            for item in st.session_state.unmatched:
                st.markdown(f"**ESPN:** {item['espn_name']} ({item['team']})")
                sel = st.selectbox("Map to FanGraphs Player:", options=item['guesses'], key=f"sel_{item['espn_name']}")
                manual_selections[item['espn_name']] = sel
                st.write("---")
            
            submitted = st.form_submit_button("Confirm Matches & Build Excel")
            
            if submitted:
                for espn_name, fg_choice in manual_selections.items():
                    if fg_choice != "Skip/Leave Blank":
                        fg_id = fg_df[fg_df['PlayerName'] == fg_choice]['playerid'].values[0]
                        st.session_state.matches[espn_name] = fg_id
                st.session_state.unmatched = [] 
                st.rerun() 
    else:
        st.success("🎉 All ESPN players have been accounted for!")
        
        if st.button("💾 Generate ESPN Merged Excel File", type="primary"):
            with st.spinner("Building Final Merged Database..."):
                output = io.BytesIO()
                
                def merge_projections(dict_list):
                    merged_list = []
                    for item in dict_list:
                        new_row = item.copy()
                        fg_id = st.session_state.matches.get(item['ESPN_Name'])
                        if fg_id:
                            fg_row = fg_df[fg_df['playerid'] == fg_id].to_dict('records')[0]
                            for col in stats_to_zscore + [f"PR_{s}" for s in stats_to_zscore] + ['Total_PR']:
                                new_row[col] = fg_row.get(col, 0.0)
                        merged_list.append(new_row)
                    return pd.DataFrame(merged_list)

                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df_all_rosters = merge_projections(st.session_state.espn_rosters)
                    for team_name, group in df_all_rosters.groupby("Fantasy Team"):
                        clean_sheet = re.sub(r'[\\/*?:\[\]]', '', team_name)[:31]
                        group.to_excel(writer, sheet_name=clean_sheet, index=False)
                        auto_adjust_column_width(writer, group, clean_sheet)

                    df_fa = merge_projections(st.session_state.espn_fa)
                    if 'Total_PR' in df_fa.columns: df_fa = df_fa.sort_values(by='Total_PR', ascending=False)
                    df_fa.to_excel(writer, sheet_name="Top Free Agents", index=False)
                    auto_adjust_column_width(writer, df_fa, "Top Free Agents")

                    df_master = merge_projections(st.session_state.master_list)
                    if 'Total_PR' in df_master.columns: df_master = df_master.sort_values(by='Total_PR', ascending=False)
                    df_master.to_excel(writer, sheet_name="Master League List", index=False)
                    auto_adjust_column_width(writer, df_master, "Master League List")

                st.session_state.final_excel_data = output.getvalue()
                st.balloons()

    if 'final_excel_data' in st.session_state:
        st.download_button(
            label="📥 Download ESPN Merged Export",
            data=st.session_state.final_excel_data,
            file_name=f"Fantasy_Rosters_With_Projections_{year}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
