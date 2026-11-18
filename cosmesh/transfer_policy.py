"""Broad, bounded transfer defaults shared by Thread and Library clients."""
MIB=1024**2
GB=1000**3
GIB=1024**3
TIB=1024**4
PERSONAL=dict(auto_file=5*GB,explicit_file=5*GB,batch_bytes=10*GB,batch_files=10000,
              daily_bytes=TIB,server_bytes=30*GB,cache_bytes=100*GIB)
PUBLIC=dict(file=2*GB,batch=5*GB,current=15*GB,retained=15*GB)
WIDE_046=dict(auto_file=20*GIB,explicit_file=20*GIB,batch_bytes=100*GIB,batch_files=10000,
              daily_bytes=TIB,server_bytes=10*TIB,cache_bytes=100*GIB)
PREVIOUS=dict(auto_file=256*MIB,explicit_file=2*GIB,batch_bytes=2*GIB,batch_files=1000,
              daily_bytes=10*GIB,server_bytes=50*GIB,cache_bytes=10*GIB)


def effective(policy, overrides=()):
    # Upgrade untouched shipped defaults; retain every user-selected deviation.
    return {key:PERSONAL[key] if key not in overrides and value in (PREVIOUS.get(key),WIDE_046.get(key)) else value for key,value in policy.items()}


def content_reason(name, head):
    from pathlib import Path
    lower=name.casefold()
    if lower.endswith(('.pem','.key')) or lower.startswith(('id_rsa','id_ed25519','credentials')) or lower=='.env' or lower.startswith('.env.'):
        return 'credential_path'
    if b'-----BEGIN ' in head and b'PRIVATE KEY-----' in head:return 'credential_content'
    # Opaque data, archives, executables and unknown extensions are stored only;
    # no decompression, import, rendering or execution is implied by acceptance.
    return None
