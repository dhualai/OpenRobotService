import os
import sys
import configparser
import argparse
import alibabacloud_oss_v2 as oss


def load_config():
    config = configparser.ConfigParser()
    if not os.path.exists('oss_config.ini'):
        print('[ERROR] oss_config.ini not found')
        sys.exit(1)
    config.read('oss_config.ini', encoding='utf-8')
    return {
        'access_key_id':      config.get('oss', 'access_key_id'),
        'access_key_secret': config.get('oss', 'access_key_secret'),
        'endpoint':           config.get('oss', 'endpoint'),
        'bucket_name':        config.get('oss', 'bucket_name'),
        'region':             config.get('oss', 'region', fallback=''),
        'upload_dir':         config.get('oss', 'upload_dir', fallback=''),
        'part_size_mb':       config.getint('oss', 'part_size_mb', fallback=10),
        'list_prefix':        config.get('oss', 'list_prefix', fallback=''),
    }


def upload_file(client, bucket_name, local_path, object_name, part_size_mb=10):
    file_size = os.path.getsize(local_path)
    file_size_mb = file_size / (1024 * 1024)

    print(f'\n[INFO] File: {local_path}')
    print(f'[INFO] Size: {file_size_mb:.2f} MB')
    print(f'[INFO] Object: {object_name}')
    print(f'[INFO] Part size: {part_size_mb} MB')

    part_size = part_size_mb * 1024 * 1024

    init_result = client.initiate_multipart_upload(
        oss.InitiateMultipartUploadRequest(bucket=bucket_name, key=object_name)
    )
    upload_id = init_result.upload_id
    print(f'[INFO] UploadId: {upload_id}')

    upload_parts = []
    part_number = 1

    with open(local_path, 'rb') as f:
        for start in range(0, file_size, part_size):
            n = part_size
            if start + n > file_size:
                n = file_size - start

            reader = oss.io_utils.SectionReader(oss.io_utils.ReadAtReader(f), start, n)
            result = client.upload_part(oss.UploadPartRequest(
                bucket=bucket_name,
                key=object_name,
                upload_id=upload_id,
                part_number=part_number,
                body=reader,
            ))

            upload_parts.append(oss.UploadPart(part_number=part_number, etag=result.etag))
            part_number += 1

            progress = (start + n) / file_size * 100
            print(f'\r[PROGRESS] {progress:.1f}%  (part {part_number - 1})', end='', flush=True)

    print()

    parts = sorted(upload_parts, key=lambda p: p.part_number)
    result = client.complete_multipart_upload(oss.CompleteMultipartUploadRequest(
        bucket=bucket_name,
        key=object_name,
        upload_id=upload_id,
        complete_multipart_upload=oss.CompleteMultipartUpload(parts=parts),
    ))

    print(f'[OK] Done: {object_name}  ETag: {result.etag}')


def list_files(client, bucket_name, prefix='', endpoint=''):
    print(f'\n[INFO] Listing: bucket={bucket_name}, prefix={prefix or "/"}')
    print('-' * 70)
    print(f'{"Object Key":<45} {"Size":>12}  {"Last Modified":<19}')
    print('-' * 70)

    total_size = 0
    count = 0
    presigned_urls = []

    paginator = client.list_objects_v2_paginator()
    for page in paginator.iter_page(oss.ListObjectsV2Request(
            bucket=bucket_name
        )):
        for obj in page.contents:
            size_mb = obj.size / (1024 * 1024)
            size_str = f'{size_mb:.2f} MB' if size_mb >= 1 else f'{obj.size} B'
            mtime = str(obj.last_modified)[:19] if obj.last_modified else 'N/A'
            
            print(f'{obj.key:<45} {size_str:>12}  {mtime:<19}')
            
            pre_result = client.presign(
                oss.GetObjectRequest(bucket=bucket_name, key=obj.key)
            )
            presigned_url = pre_result.url
            presigned_urls.append((obj.key, presigned_url))
            
            total_size += obj.size
            count += 1

    print('-' * 70)
    total_str = total_size / (1024 * 1024)
    print(f'Total: {count} objects, {total_str:.2f} MB')
    
    if presigned_urls:
        print('\n[INFO] Presigned URLs (valid for 1 hour):')
        print('-' * 70)
        for key, url in presigned_urls:
            print(f'{key}:')
            print(f'  {url}')
            print()


def main():
    cfg = load_config()

    credentials_provider = oss.credentials.StaticCredentialsProvider(
        cfg['access_key_id'],
        cfg['access_key_secret'],
    )
    sdk_cfg = oss.config.load_default()
    sdk_cfg.credentials_provider = credentials_provider
    sdk_cfg.region = cfg['region']
    sdk_cfg.endpoint = cfg['endpoint']

    client = oss.Client(sdk_cfg)

    cmd_parser = argparse.ArgumentParser(description='OSS tool')
    cmd_parser.add_argument('--list', metavar='PREFIX', nargs='?', const=True,
                            default=None,
                            help=f'List files in OSS (prefix, default: "{cfg["list_prefix"]}")')
    cmd_parser.add_argument('--file', metavar='PATH', help='Upload a single file')
    cmd_parser.add_argument('--dir', metavar='PATH', help='Upload a directory')
    cmd_parser.add_argument('--bucket', metavar='NAME', default=cfg['bucket_name'],
                            help=f'Bucket name (default: from config)')
    cmd_parser.add_argument('--upload-dir', metavar='DIR', default=cfg['upload_dir'],
                            help=f'OSS upload directory (default: "{cfg["upload_dir"]}")')
    cmd_parser.add_argument('--part-size', metavar='MB', type=int, default=cfg['part_size_mb'],
                            help=f'Part size in MB for multipart upload (default: {cfg["part_size_mb"]})')
    cmd_args = cmd_parser.parse_args()

    bucket_name = cmd_args.bucket
    upload_dir = cmd_args.upload_dir
    part_size_mb = cmd_args.part_size

    if cmd_args.list is not None:
        prefix = cfg['list_prefix'] if cmd_args.list is True else cmd_args.list
        if prefix and not prefix.endswith('/'):
            prefix += '/'
        list_files(client, bucket_name, prefix, cfg['endpoint'])
        print('\n[ALL DONE]')
        return

    if cmd_args.file:
        target = cmd_args.file
        filename = os.path.basename(target)
        object_name = os.path.join(upload_dir, filename).replace('\\', '/')
        try:
            upload_file(client, bucket_name, target, object_name, part_size_mb)
        except Exception as e:
            print(f'\n[ERROR] {e}')
            sys.exit(1)

    elif cmd_args.dir:
        target = cmd_args.dir
        print(f'[INFO] Uploading directory: {target}')
        for root, dirs, files in os.walk(target):
            for fname in sorted(files):
                local_path = os.path.join(root, fname)
                rel_path = os.path.relpath(local_path, target)
                object_name = os.path.join(upload_dir, rel_path).replace('\\', '/')
                try:
                    upload_file(client, bucket_name, local_path, object_name, part_size_mb)
                except Exception as e:
                    print(f'\n[ERROR] {fname}: {e}')
                    continue

    print('\n[ALL DONE]')


if __name__ == '__main__':
    main()