import asyncio

count = 0

async def add():
    global count

    for _ in range(10000):
        # 读取
        tmp = count

        # 故意让出控制权
        await asyncio.sleep(0)

        # 修改
        tmp = tmp + 1

        # 再次让出控制权
        await asyncio.sleep(0)

        # 写回
        count = tmp

async def main():
    global count

    tasks = [
        asyncio.create_task(add()),
        asyncio.create_task(add())
    ]

    await asyncio.gather(*tasks)

    print("最终 count =", count)

asyncio.run(main())