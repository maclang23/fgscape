import streamlit as st
import requests
import pandas as pd
import io
import traceback

st.set_page_config(page_title="FanGraphs Projections", layout="wide")
st.title("⚾ FanGraphs Projections Scraper & Z-Score Calc")

# --- UI Controls (Main Body) ---
st.subheader("⚙️ Settings")

col1, col2, col3 = st.columns(3)
with col1:
    player_type = st.radio("Select Player Type:", ["Batters", "Pitchers"], horizontal=True)
    is_pitcher = player_type == "Pitchers"
with col2:
    num_players = st.number_input("Number of Players to Return:", min_value=10, max_value=1000, value=400, step=10)
with col3:
    min_systems = st.number_input(
        "Min Systems for Consensus:", 
        min_value=1, max_value=8, value=2, step=1,
        help="Filter out players from the Consensus tab who don't appear in at least this many selected systems."
    )

st.subheader("📊 Projection Systems")

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

st.divider()

proj_map = {
    'steamer': use_steamer, 'fangraphsdc': use_fangraphsdc, 
    'thebat': use_thebat, 'thebatx': use_thebatx, 
    'atc': use_atc, 'oopsy': use_oopsy, 
    'zips': use_zips, 'zipsdc': use_zipsdc
}
active_projections = [proj for proj, is_active in proj_map.items() if is_active]

# --- Logic Configuration ---
if is_pitcher:
    stat_type = "pit"
    stats_to_keep = ['W', 'QS', 'SO', 'ERA', 'WHIP']
    stats_to_zscore = ['W', 'QS', 'SO', 'ERA', 'WHIP', 'SVHLD']
    final_cols = ['PlayerName', 'playerid', 'W', 'QS', 'SO', 'ERA', 'WHIP', 'SVHLD']
else:
    stat_type = "bat"
    stats_to_keep = ['R', 'HR', 'RBI', 'SB', 'OBP', 'SLG']
    stats_to_zscore = ['R', 'HR', 'RBI', 'SB', 'OBP', 'SLG']
    final_cols = ['PlayerName', 'playerid', 'R', 'HR', 'RBI', 'SB', 'OBP', 'SLG']

# --- App Execution ---
if st.button("Generate Projections", type="primary"):
    if not active_projections:
        st.error("Please select at least one projection system.")
    else:
        status_text = st.empty()
        dfs_to_save = {}
        all_raw_data = []
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Referer": "https://www.fangraphs.com/projections"
        }
        
        for proj in active_projections:
            status_text.info(f"⏳ Fetching data from {proj.upper()}...")
            
            url = "https://www.fangraphs.com/api/projections"
            
            # Reverted to the correct parameters you originally provided
            params = {
                "type": proj,
                "stats": stat_type,
                "pos": "all",
                "team": "0",
                "players": "0", 
                "lg": "all",
                "statgroup": "fantasy",
                "fantasypreset": "classic"
            }
            
            try:
                response = requests.get(url, params=params, headers=headers, timeout=15)
                response.raise_for_status()
                data = response.json()
                
                # Safely extract data
                if isinstance(data, dict):
                    if 'data' in data:
                        player_data = data['data']
                    else:
                        player_data = []
                elif isinstance(data, list):
                    player_data = data
                else:
                    st.warning(f"Skipping {proj.upper()}: Unexpected data format.")
                    continue
                
                if not player_data:
                    st.warning(f"No data returned for {proj}.")
                    continue
                    
                df = pd.DataFrame(player_data)
                
                # --- NEW: Dynamic Column Hunting ---
                col_map = {str(c).lower(): c for c in df.columns}
                rename_dict = {}
                
                # Hunt for Name
                for name_var in ['playername', 'name', 'fullname', 'player_name', 'player']:
                    if name_var in col_map:
                        rename_dict[col_map[name_var]] = 'PlayerName'
                        break
                        
                # Hunt for ID
                for id_var in ['playerid', 'id', 'minormasterid']:
                    if id_var in col_map:
                        rename_dict[col_map[id_var]] = 'playerid'
                        break
                
                # Map baseball stats
                for stat in stats_to_keep + (['SV', 'HLD'] if is_pitcher else []):
                    if stat.lower() in col_map:
                        rename_dict[col_map[stat.lower()]] = stat
                        
                df.rename(columns=rename_dict, inplace=True)
                
                # Ensure we actually found the player name before proceeding
                if 'PlayerName' not in df.columns:
                    st.warning(f"Skipping {proj.upper()}: Could not find a recognizable Name column. Columns found: {list(df.columns)[:15]}")
                    continue
                
                df = df.head(int(num_players))
                
                # Fill missing stats with 0.0
                for col in stats_to_keep:
                    if col not in df.columns:
                        df[col] = 0.0
                        
                if is_pitcher:
                    for col in ['SV', 'HLD']:
                        if col not in df.columns:
                            df[col] = 0.0
                    df['SVHLD'] = df['SV'] + df['HLD']
                
                # Filter to final columns safely
                existing_cols = [c for c in final_cols if c in df.columns]
                df = df[existing_cols].copy()
                
                # Calculate Z-scores
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
                
                raw_df = df[existing_cols].copy()
                raw_df['System'] = proj.upper()
                
                dfs_to_save[proj] = df
                all_raw_data.append(raw_df)
                
            except Exception as e:
                # This will print the exact line of code that failed if it crashes again
                st.error(f"🛑 CRASH in {proj.upper()}:\n\nError: {e}\n\nTraceback:\n{traceback.format_exc()}")

        # --- Build Consensus ---
        if not dfs_to_save:
            status_text.error("❌ No data was retrieved. Check your network or FanGraphs API.")
        else:
            if all_raw_data:
                status_text.info("🧮 Calculating Consensus Projections and Final Z-Scores...")
                combined_df = pd.concat(all_raw_data)
                
                agg_rules = {stat: 'mean' for stat in stats_to_zscore}
                agg_rules['System'] = lambda x: ', '.join(x)
                
                consensus_df = combined_df.groupby(['playerid', 'PlayerName'], as_index=False).agg(agg_rules)
                consensus_df.rename(columns={'System': 'Sources'}, inplace=True)
                
                consensus_df['System_Count'] = consensus_df['Sources'].apply(lambda x: len(x.split(', ')))
                consensus_df = consensus_df[consensus_df['System_Count'] >= min_systems]
                consensus_df.drop(columns=['System_Count'], inplace=True)
                
                if consensus_df.empty:
                    st.warning(f"No players appeared in at least {min_systems} systems.")
                else:
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
                    
                    cols = consensus_df.columns.tolist()
                    cols.insert(2, cols.pop(cols.index('Sources')))
                    consensus_df = consensus_df[cols]
                    
                    dfs_to_save['Consensus'] = consensus_df

            if 'Consensus' in dfs_to_save and not dfs_to_save['Consensus'].empty:
                st.session_state['preview_df'] = dfs_to_save['Consensus'].head(10)
                st.session_state['preview_title'] = "Top 10 Consensus Preview"
            elif active_projections and active_projections[0] in dfs_to_save:
                st.session_state['preview_df'] = dfs_to_save[active_projections[0]].head(10)
                st.session_state['preview_title'] = f"Top 10 {active_projections[0].upper()} Preview (Consensus Unavailable)"

            status_text.info("💾 Packaging Excel File...")
            excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
                if 'Consensus' in dfs_to_save and not dfs_to_save['Consensus'].empty:
                    dfs_to_save['Consensus'].to_excel(writer, sheet_name='Consensus', index=False)
                for proj, df in dfs_to_save.items():
                    if proj != 'Consensus' and not df.empty:
                        df.to_excel(writer, sheet_name=proj, index=False)
            
            st.session_state['excel_data'] = excel_buffer.getvalue()
            st.session_state['file_name'] = f"fangraphs_{player_type.lower()}_projections.xlsx"
            
            status_text.success("✅ Projections generated successfully!")

# --- Display Preview and Download Button ---
if 'preview_df' in st.session_state:
    st.subheader(f"👀 {st.session_state['preview_title']}")
    st.dataframe(st.session_state['preview_df'], use_container_width=True, hide_index=True)

if 'excel_data' in st.session_state:
    st.download_button(
        label="📥 Download Full Excel File",
        data=st.session_state['excel_data'],
        file_name=st.session_state['file_name'],
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
