# RCSB PDB Downloader
An In-house python script for downloading the PDB files from RCSB Website directly.

## Requirements for this script to run
* Python-3.10.12   
* requests   
* tqdm

## How to use it

***Script 1***

1. Directly on the command line   
python rcsb_pdb_download_by_pdbid.py --ids 1ABC 2DEF 3GHI

2. From a text file   
python rcsb_pdb_download_by_pdbid.py --file my_list.txt

3. Both at once — it merges and deduplicates   
python rcsb_pdb_download_by_pdbid.py --ids 1ABC --file my_list.txt

***Script 1***

1. Download all PDB files released between two dates   
python rcsb_pdb_downloader_by_between_dates.py --start 2024-01-01 --end 2024-06-30

2. Just get the ID list first, without downloading files (fast check)   
python rcsb_pdb_downloader_by_between_dates.py --start 2024-01-01 --end 2024-06-30 --ids-only

3. Download as .cif format instead of .pdb   
python rcsb_pdb_downloader_by_between_dates.py --start 2024-01-01 --end 2024-06-30 --format cif

4. Save to a custom folder   
python rcsb_pdb_downloader_by_between_dates.py --start 2024-01-01 --end 2024-06-30 --output-dir /my/data/folder

  Note: It is made as very easy based on their RCSB API key in the main website. It is very much useful for R & D Laboratories. We can improve it any point of time, only through study the API key from their wewsite. 

Hence, I have done it successfully!!!✨🫰😍
